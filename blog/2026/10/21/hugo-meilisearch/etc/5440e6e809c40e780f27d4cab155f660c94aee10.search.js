/*!
 * Search, v1.0.0
 *
 * Searching functionality for a Hugo-based website:
 *
 * 1. Meilisearch as the main search (https://decovar.dev/blog/2026/10/21/hugo-meilisearch/);
 * 2. Basic search as a fallback (https://decovar.dev/blog/2020/01/05/hugo-search/).
 *
 * Author: retif, Declaration of VAR
 * License: AGPLv3
 */

// When the page has the `searchEndpoint` and `searchIndex` meta tags (set by the `params.search`
// in the site config), queries go to Meilisearch through that endpoint (the web server should
// forward them there), and the results link straight to the page sections. Without them, or once
// Meilisearch turns out to be unavailable, it falls back to the basic search with index.json.
// Too many requests (the web server rate limiting) or a busy Meilisearch are only temporary,
// so just that one query would fall back to the basic search.

var sidebars = document.getElementById("sidebars");
var searchResults = document.getElementById("search-results");
var searchInput = document.getElementById("search-query");

var searchEndpoint = metaContent("searchEndpoint");
var searchIndex = metaContent("searchIndex");
// stays false for the rest of the page lifetime once Meilisearch can't serve this page
// (but not when it is only temporarily busy)
var useMeilisearch = Boolean(searchEndpoint && searchIndex);

// web server rate limiting (429) and Meilisearch search queue being full (503)
var temporaryFailureStatuses = [429, 503];

var minQueryLengthMeilisearch = 2;
var minQueryLengthBasic = 3;
var maxSectionsPerPage = 3;
var contextDive = 40;
// Meilisearch wraps the matches into these, and since they can't occur in the indexed text,
// splitting the snippets by them finds the matches, which then become <mark> elements
var highlightPreTag = "\u0001";
var highlightPostTag = "\u0002";

var timerUserInput = false;
var searchGeneration = 0; // results of anything but the latest search are dropped
var pendingRequest = null; // AbortController of the Meilisearch request in flight
var basicIndex = null; // promise of the index.json contents, fetched only once

searchInput.addEventListener("input", function ()
{
    // don't start searching every time a key is pressed,
    // wait a bit till user stops typing
    if (timerUserInput) { clearTimeout(timerUserInput); }
    timerUserInput = setTimeout(
        function () { search(searchInput.value.trim()); },
        useMeilisearch ? 200 : 500
    );
});

function metaContent(name)
{
    let meta = document.getElementsByName(name)[0];
    return meta ? meta.content : "";
}

function search(searchQuery)
{
    searchGeneration++;
    if (pendingRequest) { pendingRequest.abort(); pendingRequest = null; }

    // ignore empty and short search queries
    let minQueryLength = useMeilisearch ? minQueryLengthMeilisearch : minQueryLengthBasic;
    if (searchQuery.length < minQueryLength)
    {
        clearResults();
        searchResults.style.display = "none";
        sidebars.style.display = "block";
        return;
    }

    sidebars.style.display = "none";
    searchResults.style.display = "block";

    if (useMeilisearch) { searchMeilisearch(searchQuery, searchGeneration); }
    else { searchBasic(searchQuery, searchGeneration); }
}

function clearResults()
{
    while (searchResults.firstChild) { searchResults.removeChild(searchResults.firstChild); }
}

function showMessage(text, className)
{
    clearResults();
    searchResults.appendChild(messageElement(text, className));
}

function messageElement(text, className)
{
    return makeElement("div", className ? { "class": className } : {}, [makeElement("b", {}, [text])]);
}

// children are elements or strings, and strings become text, so they need no escaping
function makeElement(tag, attributes, children)
{
    let result = document.createElement(tag);
    Object.keys(attributes || {}).forEach(function (name)
    {
        result.setAttribute(name, attributes[name]);
    });
    (children || []).forEach(function (child) { result.append(child); });
    return result;
}

//
// Meilisearch
//

function searchMeilisearch(searchQuery, generation)
{
    let controller = new AbortController();
    pendingRequest = controller;

    fetch(searchEndpoint.concat("indexes/", encodeURIComponent(searchIndex), "/search"),
    {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
            q: searchQuery,
            limit: 50,
            attributesToRetrieve: ["url", "page_title", "breadcrumbs", "date"],
            attributesToHighlight: ["page_title", "hierarchy", "content"],
            attributesToCrop: ["content"],
            cropLength: 24,
            highlightPreTag: highlightPreTag,
            highlightPostTag: highlightPostTag
        })
    })
    .then(function (response)
    {
        // error responses are JSON too, but anything else (such as an HTML page) means no body
        return response.json()
            .catch(function () { return null; })
            .then(function (body) { return { status: response.status, body: body }; });
    })
    .then(function (result)
    {
        if (generation !== searchGeneration) { return; }
        pendingRequest = null;

        let code = result.body && result.body.code ? result.body.code : "no details";
        if (result.status === 200 && result.body && Array.isArray(result.body.hits))
        {
            showMeilisearchResults(result.body.hits);
        }
        else if (temporaryFailureStatuses.includes(result.status))
        {
            console.warn("Meilisearch search is busy (".concat(result.status, ", ", code, "), using the basic search for this query only"));
            fallBackToBasicSearch(searchQuery, generation, false);
        }
        else
        {
            // the index is gone, the web server doesn't forward the requests (such as with
            // the local `hugo server`), or the search is not available at the moment
            console.warn("Meilisearch search is not available (".concat(result.status, ", ", code, "), falling back to the basic search"));
            fallBackToBasicSearch(searchQuery, generation, true);
        }
    })
    .catch(function (error)
    {
        if (error.name === "AbortError" || generation !== searchGeneration) { return; }
        pendingRequest = null;
        console.warn("Meilisearch search failed (".concat(error, "), falling back to the basic search"));
        fallBackToBasicSearch(searchQuery, generation, true);
    });
}

function fallBackToBasicSearch(searchQuery, generation, permanently)
{
    if (permanently) { useMeilisearch = false; }
    if (searchQuery.length < minQueryLengthBasic) { showMessage("Nothing found"); }
    else { searchBasic(searchQuery, generation); }
}

function showMeilisearchResults(hits)
{
    if (hits.length === 0)
    {
        showMessage("Nothing found");
        return;
    }

    // hits are sections, so group them by page, keeping the order of their best ranked sections
    let pages = [];
    let pagesByPath = {};
    hits.forEach(function (hit)
    {
        let path = hit.url.split("#")[0];
        let page = pagesByPath[path];
        if (!page)
        {
            page = { path: path, hit: hit, sections: [] };
            pagesByPath[path] = page;
            pages.push(page);
        }
        if (page.sections.length < maxSectionsPerPage
            && !page.sections.some(function (s) { return s.url === hit.url; }))
        {
            page.sections.push(hit);
        }
    });

    clearResults();
    searchResults.appendChild(messageElement("Found: ".concat(pages.length)));

    pages.forEach(function (page)
    {
        let summary = makeElement("div", { "class": "search-summary" }, [
            makeElement("h4", { "class": "post-title" }, [
                makeElement("a", { "href": page.path }, highlighted(page.hit._formatted.page_title))
            ])
        ]);
        // where the page is and when it was published, which tells blog posts and news apart
        let info = (page.hit.breadcrumbs || []).join(" › ");
        if (page.hit.date)
        {
            info = info.length > 0 ? info.concat(" · ", page.hit.date) : String(page.hit.date);
        }
        if (info.length > 0)
        {
            summary.appendChild(makeElement("div", { "class": "search-page-info" }, [info]));
        }
        let sections = makeElement("ul", { "class": "search-sections" });
        page.sections.forEach(function (section)
        {
            let hierarchy = section._formatted.hierarchy || [];
            let item = makeElement("li");
            if (hierarchy.length > 0)
            {
                let headings = [];
                hierarchy.forEach(function (heading, index)
                {
                    if (index > 0) { headings.push(" › "); }
                    headings = headings.concat(highlighted(heading));
                });
                item.appendChild(makeElement("a", { "href": section.url }, headings));
            }
            if (section._formatted.content)
            {
                item.appendChild(makeElement("p", {}, highlighted(section._formatted.content)));
            }
            sections.appendChild(item);
        });
        summary.appendChild(sections);
        searchResults.appendChild(summary);
    });
}

// the text as strings and <mark> elements, to be used as children of an element
function highlighted(text)
{
    let nodes = [];
    String(text).split(highlightPreTag).forEach(function (part, index)
    {
        // every part but the first one starts with a match, which lasts till the post tag
        let pieces = part.split(highlightPostTag);
        let match = index > 0 ? pieces.shift() : "";
        let rest = pieces.join("");
        if (match.length > 0) { nodes.push(makeElement("mark", {}, [match])); }
        if (rest.length > 0) { nodes.push(rest); }
    });
    return nodes;
}

//
// basic search with index.json
//

function loadBasicIndex()
{
    if (!basicIndex)
    {
        basicIndex = fetch("/index.json")
            .then(function (response)
            {
                if (!response.ok) { throw new Error("HTTP ".concat(response.status)); }
                return response.json();
            });
        // so the next search tries to load it again
        basicIndex.catch(function () { basicIndex = null; });
    }
    return basicIndex;
}

function searchBasic(searchQuery, generation)
{
    loadBasicIndex()
    .then(function (contents)
    {
        if (generation === searchGeneration) { showBasicResults(contents, searchQuery); }
    })
    .catch(function (error)
    {
        console.error("Some error processing /index.json: ".concat(error));
        if (generation === searchGeneration)
        {
            showMessage("Search error", "search-error");
        }
    });
}

function showBasicResults(contents, searchQuery)
{
    clearResults();

    var results = [];
    let regex = new RegExp(escapeRegExp(searchQuery), "i");
    // iterate through posts and collect the ones with matches
    contents.forEach(function(post)
    {
        if (post.title.match(regex) || post.content.match(regex))
        {
            results.push(post);
        }
    });

    if (results.length > 0)
    {
        searchResults.appendChild(messageElement("Found: ".concat(results.length)));

        // populate search results block with excerpts around the matched search query
        results.forEach(function (value)
        {
            let firstIndexOf = value.content.toLowerCase().indexOf(searchQuery.toLowerCase());
            let lastIndexOf = firstIndexOf + searchQuery.length;
            let spaceIndex = firstIndexOf - contextDive;
            if (spaceIndex > 0)
            {
                spaceIndex = value.content.indexOf(" ", spaceIndex) + 1;
                if (spaceIndex < firstIndexOf) { firstIndexOf = spaceIndex; }
                else { firstIndexOf = firstIndexOf - contextDive / 2; }
            }
            else
            {
                firstIndexOf = 0;
            }
            let lastSpaceIndex = lastIndexOf + contextDive;
            if (lastSpaceIndex < value.content.length)
            {
                lastSpaceIndex = value.content.indexOf(" ", lastSpaceIndex);
                if (lastSpaceIndex !== -1) { lastIndexOf = lastSpaceIndex; }
                else { lastIndexOf = lastIndexOf + contextDive / 2; }
            }
            else
            {
                lastIndexOf = value.content.length - 1;
            }

            let summary = value.content.substring(firstIndexOf, lastIndexOf);
            if (firstIndexOf !== 0) { summary = "...".concat(summary); }
            if (lastIndexOf !== value.content.length - 1) { summary = summary.concat("..."); }

            let div = makeElement("div", { "class": "search-summary" }, [
                makeElement("h4", { "class": "post-title" }, [
                    makeElement("a", { "href": value.permalink }, [value.title])
                ]),
                makeElement("p", {}, [summary])
            ]);
            searchResults.appendChild(div);

            // optionally highlight the search query in excerpts using mark.js (https://markjs.io)
            new Mark(div).mark(searchQuery, { "separateWordSearch": false });
        });
    }
    else
    {
        searchResults.appendChild(messageElement("Nothing found"));
    }
}

// what the user types is a plain text to look for, not a regular expression
function escapeRegExp(text)
{
    return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
