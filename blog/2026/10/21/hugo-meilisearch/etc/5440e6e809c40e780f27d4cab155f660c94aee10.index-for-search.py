"""
Indexes the website in Meilisearch, from the `search-records.json` that Hugo generates
in the build output (the `SearchRecords` output format, see `params.search` in `config.toml`).

It runs on the Meilisearch host, where Meilisearch listens on localhost only, after the website
is deployed, piping the script from the repository over SSH, for example:

``` sh
$ ssh buildbot@YOUR-host "python - --records /var/www/YOUR-WEBSITE/search-records.json --key-file ~/YOUR-WEBSITE-index-writer.key" < ./scripts/index-for-search.py
```

The website has only one version, so there is only one index, named by Hugo (`params.search.index`),
and every run builds it from scratch: a staging index gets the settings and the documents first,
and only then swaps places with the live one. That way searches never get an incomplete index,
and a failed run leaves the previous one serving (or no index at all, if there was none before,
so the pages fall back to the basic search). After the swap the staging index has the previous
contents, so it gets deleted.

The Meilisearch API key it needs allows `indexes.create`, `indexes.delete`, `indexes.get`,
`indexes.swap`, `settings.update`, `documents.add` and `tasks.get`, and its `indexes` has to be
`["*"]`: an index swap is a global task (not bound to an index), which a key scoped to particular
indexes can enqueue, but can't get, so it would never know whether the swap succeeded.

Problems with the records or with Meilisearch are reported as errors with the exit code 1, while
anything else (which would be a bug in this script) ends up with a traceback. Requires Python 3.10
or newer.

<https://decovar.dev/blog/2026/10/21/hugo-meilisearch/>

Author: retif, Declaration of VAR
License: AGPLv3
"""

import argparse
import bisect
import html.parser
import http.client
import itertools
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

recordsFormat = 1
recordsFileName = "search-records.json"
stagingIndexSuffix = "-staging"
# what the Meilisearch API key has to allow (and its `indexes` has to be `["*"]`, see above)
apiKeyActions = (
    "indexes.create",
    "indexes.delete",
    "indexes.get",
    "indexes.swap",
    "settings.update",
    "documents.add",
    "tasks.get"
)
# sections longer than that get split into several records, which keeps the search results
# snippets/crops relevant. Code blocks up to that size are never split, so a record
# can get somewhat longer
maxWordsPerRecord = 300
documentsBatchSize = 1000
requestTimeout = 60  # seconds
taskTimeout = 600  # seconds

indexSettings = {
    # the order matters, because matches in earlier attributes rank higher. Breadcrumbs
    # are not searchable, because nearly all the records have the same ones (`Blog posts`),
    # so searching for those words would match everything
    "searchableAttributes": [
        "page_title",
        "hierarchy",
        "content"
    ],
    "displayedAttributes": [
        "url",
        "page_title",
        "hierarchy",
        "breadcrumbs",
        "content",
        "section",
        "date",
        "tags",
        "chunk"
    ],
    # `url` and `chunk` let clients fetch all the records of a split section in order
    "filterableAttributes": [
        "url",
        "section",
        "tags"
    ],
    "sortableAttributes": [
        "chunk"
    ],
    # dictionary is needed because the separators would otherwise reduce C++ and C#
    # to just `c`, and also dictionary matches are case-sensitive, so a query in lowercase
    # needs its own entries. At the same time, `.NET` can't have it, because `.net` would
    # also match every `something.net` link
    "dictionary": ["C#", "c#", "C++", "c++"],
    # the content is in english, except for the news and some of the posts and texts,
    # which are in russian, so don't let language detection guess (wrongly) from the code
    # snippets and identifiers
    "localizedAttributes": [
        {
            "attributePatterns": ["*"],
            "locales": ["eng", "rus"]
        }
    ]
}

# these break words (or lines), so text on both sides of them must not get glued together
blockTags = {
    "address", "article", "aside", "blockquote", "br", "dd", "details", "div", "dl",
    "dt", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "li", "main", "ol", "p", "pre", "section", "summary", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "ul"
}
# contents of these are not searchable text
skippedTags = {"script", "style", "template", "svg"}
# and neither are contents of elements with these classes: the `video` shortcode
# adds the same download link paragraph under every video
skippedClasses = {"video-fallback"}
headingTags = {"h1", "h2", "h3", "h4", "h5", "h6"}


class IndexingError(Exception):
    pass


class MeilisearchError(IndexingError):
    def __init__(self, status: int, code: str, message: str, hint: str = ""):
        super().__init__(f"{message} (HTTP {status}, {code})" + (f" - {hint}" if hint else ""))
        self.status = status
        self.code = code


#
# reporting
#

def report(text: str):
    print(text, flush=True)


def reportWarning(text: str):
    print(f"[WARNING] {text}", file=sys.stderr, flush=True)


def reportError(text: str):
    print(f"[ERROR] {text}", file=sys.stderr, flush=True)


#
# splitting pages into records
#

class SectionsParser(html.parser.HTMLParser):
    """
    Splits the rendered content of a page into sections at the headings that have an id
    (which Hugo gives to all the Markdown headings), so search results can link straight
    to the section. Headings without an id stay part of the text. Code blocks (`<pre>`)
    become separate segments of their section, so records can be split around them.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        # (anchor, [heading texts from the top level down to this section],
        # [(is code, text) segments in the document order])
        self.sections: list[tuple[str, list[str], list[tuple[bool, str]]]] = []
        self._anchor = ""
        self._hierarchy: list[tuple[int, str]] = []  # (heading level, heading text)
        self._segments: list[tuple[bool, str]] = []
        self._textParts: list[str] = []
        # text parts of the code block that is being read, None outside of code blocks
        self._codeParts: list[str] | None = None
        # heading that is being read: (level, id, text parts)
        self._heading: tuple[int, str, list[str]] | None = None
        # element whose contents are being skipped, and how deep it is nested into itself
        self._skippedTag: str | None = None
        self._skippedDepth = 0

    def handle_starttag(self, tag, attrs):
        if self._skippedTag:
            if tag == self._skippedTag:
                self._skippedDepth += 1
            return
        attributes = dict(attrs)
        if (
            tag in skippedTags
            # table of contents (the `toc` shortcode) only repeats the headings
            or (tag == "nav" and attributes.get("id") == "TableOfContents")
            or not skippedClasses.isdisjoint((attributes.get("class") or "").split())
        ):
            self._skippedTag = tag
            self._skippedDepth = 1
            return
        if tag in headingTags and attributes.get("id") and not self._heading:
            self._finishSection()
            self._heading = (int(tag[1]), attributes["id"], [])
            return
        if tag == "pre" and self._codeParts is None and not self._heading:
            self._finishText()
            self._codeParts = []
            return
        if tag in blockTags:
            self._appendBreak()

    def handle_startendtag(self, tag, attrs):
        if not self._skippedTag and tag in blockTags:
            self._appendBreak()

    def handle_endtag(self, tag):
        if self._skippedTag:
            if tag == self._skippedTag:
                self._skippedDepth -= 1
                if self._skippedDepth == 0:
                    self._skippedTag = None
            return
        if self._heading and tag == f"h{self._heading[0]}":
            level, anchor, parts = self._heading
            self._heading = None
            self._hierarchy = [h for h in self._hierarchy if h[0] < level]
            self._hierarchy.append((level, normalizeWhitespace("".join(parts))))
            self._anchor = anchor
            return
        if tag == "pre" and self._codeParts is not None:
            self._finishCode()
            return
        if tag in blockTags:
            self._appendBreak()

    def handle_data(self, data):
        if not self._skippedTag:
            self._appendText(data)

    def close(self):
        super().close()
        self._finishSection()

    def _appendText(self, text: str):
        if self._heading:
            self._heading[2].append(text)
        elif self._codeParts is not None:
            self._codeParts.append(text)
        else:
            self._textParts.append(text)

    def _appendBreak(self):
        # code keeps its line breaks, which `<br>` is one of
        self._appendText("\n" if self._codeParts is not None and not self._heading else " ")

    def _finishText(self):
        text = normalizeWhitespace("".join(self._textParts))
        if text:
            self._segments.append((False, text))
        self._textParts = []

    def _finishCode(self):
        code = normalizeCode("".join(self._codeParts or []))
        if code:
            self._segments.append((True, code))
        self._codeParts = None

    def _finishSection(self):
        if self._codeParts is not None:
            self._finishCode()
        self._finishText()
        # a heading without text under it (right before a subheading) is still worth
        # finding, but the text before the first heading only if there is some
        if self._segments or self._anchor:
            self.sections.append(
                (self._anchor, [h[1] for h in self._hierarchy], self._segments)
            )
        self._segments = []


def normalizeWhitespace(text: str) -> str:
    return " ".join(text.split())


def normalizeCode(code: str) -> str:
    # line breaks stay, as they end `//` comments and keep the listings readable,
    # but the indentation goes: in search records it means nothing,
    # while it would add about a third to their size
    return "\n".join(
        normalizeWhitespace(line) for line in code.splitlines() if line.strip()
    )


def countWords(text: str) -> int:
    return len(text.split())


def splitIntoChunks(segments: list[tuple[bool, str]], maxWords: int) -> list[str]:
    # pieces that chunks can't be split inside of: words of the text, and code blocks,
    # or the lines of those code blocks that wouldn't fit into a chunk at all
    pieces: list[tuple[str, int, bool]] = []  # (text, words count, is code)
    for isCode, text in segments:
        if not isCode:
            pieces.extend((word, 1, False) for word in text.split(" "))
            continue
        lines = text.split("\n") if countWords(text) > maxWords else [text]
        pieces.extend((line, countWords(line), True) for line in lines)
    starts = [0]  # words count before every piece
    for _, wordsCount, _ in pieces:
        starts.append(starts[-1] + wordsCount)
    total = starts[-1]

    # evenly sized chunks, so there is no tiny one at the end, with every split
    # moved to the edge of the piece that is the nearest to it
    cuts = [0]
    for remaining in range(math.ceil(total / maxWords), 1, -1):
        if cuts[-1] + 1 >= len(pieces):
            break
        target = starts[cuts[-1]] + (total - starts[cuts[-1]]) / remaining
        cut = bisect.bisect_left(starts, target, cuts[-1] + 1, len(pieces) - 1)
        if cut > cuts[-1] + 1 and target - starts[cut - 1] <= starts[cut] - target:
            cut -= 1
        cuts.append(cut)
    cuts.append(len(pieces))

    chunks = []
    for begin, end in itertools.pairwise(cuts):
        parts = []
        for i in range(begin, end):
            if i > begin:
                # a line break after the code, or else a `//` comment in its last
                # line would swallow the text that follows
                parts.append("\n" if pieces[i][2] or pieces[i - 1][2] else " ")
            parts.append(pieces[i][0])
        chunks.append("".join(parts))
    return chunks


def makeRecords(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for pageNumber, page in enumerate(pages):
        parser = SectionsParser()
        parser.feed(page["content"])
        parser.close()
        for sectionNumber, (anchor, hierarchy, segments) in enumerate(parser.sections):
            url = f"{page['path']}#{anchor}" if anchor else page["path"]
            for chunkNumber, chunk in enumerate(splitIntoChunks(segments, maxWordsPerRecord)):
                records.append({
                    # indexes are always built from scratch, so ids only need to be unique
                    "id": f"{pageNumber}-{sectionNumber}-{chunkNumber}",
                    "url": url,
                    "page_title": page["title"],
                    "hierarchy": hierarchy,
                    "breadcrumbs": page["breadcrumbs"],
                    "content": chunk,
                    "section": page["section"],
                    "date": page["date"],
                    "tags": page["tags"],
                    "chunk": chunkNumber
                })
    return records


#
# Meilisearch API
#

class Meilisearch:
    def __init__(self, url: str, apiKey: str):
        self.url = url.rstrip("/")
        self.apiKey = apiKey

    def request(self, method: str, path: str, action: str, body: Any = None) -> Any:
        """`action` is what the API key has to allow for this request"""
        httpRequest = urllib.request.Request(
            f"{self.url}{path}",
            data=None if body is None else json.dumps(body).encode("utf-8"),
            method=method,
            headers={
                "Authorization": f"Bearer {self.apiKey}",
                "Content-Type": "application/json"
            }
        )
        try:
            with urllib.request.urlopen(httpRequest, timeout=requestTimeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as ex:
            try:
                error = json.loads(ex.read())
            except (OSError, ValueError, http.client.HTTPException):
                error = {}
            code = error.get("code", "unknown_error")
            hint = ""
            # Meilisearch says that both about a wrong key and about a valid one
            # that lacks the permission for this request
            if code == "invalid_api_key":
                hint = (
                    f"either the key is wrong, or it doesn't allow `{action}`. The key needs "
                    f"{', '.join(apiKeyActions)} actions and \"indexes\": [\"*\"]"
                )
            raise MeilisearchError(
                ex.code,
                code,
                error.get("message", f"{method} {path} failed"),
                hint
            ) from None
        except urllib.error.URLError as ex:
            raise IndexingError(
                f"Couldn't connect to Meilisearch at {self.url}: {ex.reason}"
            ) from None
        # the connection broke or timed out after it was established
        except (OSError, http.client.HTTPException) as ex:
            raise IndexingError(
                f"{method} {path} to Meilisearch at {self.url} failed: {ex!r}"
            ) from None
        try:
            return json.loads(payload) if payload else None
        except ValueError:
            raise IndexingError(
                f"{method} {path} got a response from Meilisearch that is not JSON"
            ) from None

    def waitForTask(self, enqueuedTask: dict[str, Any]) -> dict[str, Any]:
        taskUid = enqueuedTask["taskUid"]
        deadline = time.monotonic() + taskTimeout
        delay = 0.1
        while True:
            task = self.request("GET", f"/tasks/{taskUid}", "tasks.get")
            if task["status"] == "succeeded":
                return task
            if task["status"] in ("failed", "canceled"):
                error = task.get("error") or {}
                raise IndexingError(
                    f"Meilisearch task {taskUid} ({task['type']}) {task['status']}: "
                    f"{error.get('message', 'no details')}"
                )
            if time.monotonic() > deadline:
                raise IndexingError(
                    f"Meilisearch task {taskUid} ({task['type']}) "
                    f"didn't finish in {taskTimeout} seconds"
                )
            time.sleep(delay)
            delay = min(delay * 2, 2)

    def indexExists(self, uid: str) -> bool:
        try:
            self.request("GET", f"/indexes/{urllib.parse.quote(uid)}", "indexes.get")
        except MeilisearchError as ex:
            if ex.code == "index_not_found":
                return False
            raise
        return True

    def createIndex(self, uid: str):
        self.waitForTask(
            self.request("POST", "/indexes", "indexes.create", {"uid": uid, "primaryKey": "id"})
        )

    def deleteIndex(self, uid: str):
        self.waitForTask(
            self.request("DELETE", f"/indexes/{urllib.parse.quote(uid)}", "indexes.delete")
        )

    def swapIndexes(self, uid: str, otherUid: str):
        self.waitForTask(
            self.request("POST", "/swap-indexes", "indexes.swap", [{"indexes": [uid, otherUid]}])
        )

    def updateSettings(self, uid: str, settings: dict[str, Any]):
        self.waitForTask(
            self.request(
                "PATCH", f"/indexes/{urllib.parse.quote(uid)}/settings", "settings.update", settings
            )
        )

    def addDocuments(self, uid: str, documents: list[dict[str, Any]]):
        # enqueue all the batches first, they are processed in order anyway
        tasks = [
            self.request(
                "POST",
                f"/indexes/{urllib.parse.quote(uid)}/documents",
                "documents.add",
                documents[i:i + documentsBatchSize]
            )
            for i in range(0, len(documents), documentsBatchSize)
        ]
        for task in tasks:
            self.waitForTask(task)


#
# indexing
#

def loadBuild(recordsPath: str) -> dict[str, Any]:
    try:
        with open(recordsPath, encoding="utf-8") as f:
            build = json.load(f)
    except (OSError, ValueError) as ex:
        raise IndexingError(f"Couldn't read {recordsPath}: {ex}") from None
    if build.get("format") != recordsFormat:
        raise IndexingError(
            f"{recordsPath} has records format {build.get('format')}, "
            f"but this script reads only {recordsFormat}"
        )
    uid = build.get("index")
    # the staging index gets a suffix, so the uid has to leave some room for it
    # in the 400 bytes that Meilisearch allows
    if not isinstance(uid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,300}", uid):
        raise IndexingError(
            f"{recordsPath} has an invalid index uid [{uid}], "
            "Meilisearch allows only [A-Za-z0-9_-] characters"
        )
    if not build.get("pages"):
        raise IndexingError(
            f"{recordsPath} has no pages, and replacing the index with an empty one "
            "would leave the website without search"
        )
    return build


def indexSite(meilisearch: Meilisearch, build: dict[str, Any], source: str):
    uid = build["index"]
    stagingUid = f"{uid}{stagingIndexSuffix}"

    started = time.monotonic()
    records = makeRecords(build["pages"])
    report(f"Split {len(build['pages'])} pages of {source} into {len(records)} search records")

    if meilisearch.indexExists(stagingUid):
        report(f"Deleting the staging index {stagingUid} left over from a failed run")
        meilisearch.deleteIndex(stagingUid)
    meilisearch.createIndex(stagingUid)
    createdLiveIndex = False
    swapped = False
    try:
        meilisearch.updateSettings(stagingUid, indexSettings)
        meilisearch.addDocuments(stagingUid, records)
        # a swap needs both indexes to exist, so the first run creates an empty one
        if not meilisearch.indexExists(uid):
            report(f"Index {uid} doesn't exist yet, creating it")
            meilisearch.createIndex(uid)
            createdLiveIndex = True
        meilisearch.swapIndexes(uid, stagingUid)
        swapped = True
        report(f"Indexed {uid} in {time.monotonic() - started:.1f} seconds")
    finally:
        # the staging index is not needed either way: after the swap it has the previous
        # contents of the live index, and if indexing failed before the swap, then it has
        # the incomplete new ones, while the live index stays as it was, so the search
        # keeps working with the previous contents
        unneededUids = [stagingUid]
        # but if there were no previous contents, then the live index that this run created
        # is empty, which would answer every search with nothing found, while without it
        # the pages fall back to the basic search. In case the swap did happen after all
        # (it timed out or the connection broke), deleting the live index still ends up
        # only with that fallback, and the next run fixes it
        if createdLiveIndex and not swapped:
            report(f"Deleting the empty index {uid} that this run created")
            unneededUids.append(uid)
        for unneededUid in unneededUids:
            try:
                meilisearch.deleteIndex(unneededUid)
            except IndexingError as ex:
                reportWarning(
                    f"Couldn't delete the index {unneededUid} (the next run will): {ex}"
                )


def readApiKey(keyFile: str) -> str:
    try:
        with open(keyFile, encoding="utf-8") as f:
            apiKey = f.read().strip()
    except OSError as ex:
        raise IndexingError(f"Couldn't read the Meilisearch API key: {ex}") from None
    if not apiKey:
        raise IndexingError(f"Meilisearch API key file {keyFile} is empty")
    return apiKey


def main() -> int:
    argParser = argparse.ArgumentParser(
        prog="index-for-search",
        description=(
            "Indexes the website in Meilisearch from its search records "
            f"({recordsFileName} in the Hugo build output), replacing the previous index."
        ),
        allow_abbrev=False
    )
    argParser.add_argument(
        "--records",
        required=True,
        metavar=f"/path/to/{recordsFileName}",
        help="search records of the website build to index"
    )
    argParser.add_argument(
        "--key-file",
        metavar="/path/to/writer.key",
        help="file with the Meilisearch API key (required unless --dry-run)"
    )
    argParser.add_argument(
        "--url",
        default="http://127.0.0.1:7700",
        help="Meilisearch URL (default: %(default)s)"
    )
    argParser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "only split the pages into search records and report on them, "
            "without contacting Meilisearch"
        )
    )
    cliArgs = argParser.parse_args()
    if not cliArgs.dry_run and not cliArgs.key_file:
        argParser.error("--key-file is required unless --dry-run is given")

    try:
        build = loadBuild(cliArgs.records)
        if cliArgs.dry_run:
            records = makeRecords(build["pages"])
            longest = max((countWords(r["content"]) for r in records), default=0)
            report(
                f"{cliArgs.records}: index {build['index']}, {len(build['pages'])} pages, "
                f"{len(records)} search records (the longest has {longest} words)"
            )
        else:
            meilisearch = Meilisearch(cliArgs.url, readApiKey(cliArgs.key_file))
            indexSite(meilisearch, build, cliArgs.records)
    except IndexingError as ex:
        reportError(f"Search indexing failed: {ex}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
