var sidebars = document.getElementById("sidebars");
var searchResults = document.getElementById("search-results");
var searchInput = document.getElementById("search-query");

var contextDive = 40;

var timerUserInput = false;
searchInput.addEventListener("keyup", function()
{
    // don't start searching every time a key is pressed,
    // wait a bit till users stops typing
    if (timerUserInput) { clearTimeout(timerUserInput); }
    timerUserInput = setTimeout(function () { search(searchInput.value.trim()); }, 500);
});

function search(searchQuery)
{
    // clear previous search results
    while (searchResults.firstChild) { searchResults.removeChild(searchResults.firstChild); }

    // ignore empty and short search queries
    if (searchQuery.length === 0 || searchQuery.length < 3)
    {
        searchResults.style.display = "none";
        sidebars.style.display = "block";
        return;
    }

    sidebars.style.display = "none";
    searchResults.style.display = "block";

    // load our index file
    getJSON("/index.json", function (contents)
    {
        var results = [];
        let regex = new RegExp(searchQuery, "i");
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
            searchResults.appendChild(htmlToElement("<div><b>Found: ".concat(results.length, "</b></div>")));

            // populate search results block with excerpts around the matched search query
            results.forEach(function (value, key)
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

                let div = "".concat("<div id=\"search-summary-", key, "\">")
                    .concat("<h4 class=\"post-title\"><a href=\"", value.permalink, "\">", value.title, "</a></h4>")
                    .concat("<p>", summary, "</p>")
                    .concat("</div>");
                searchResults.appendChild(htmlToElement(div));

                // optionaly highlight the search query in excerpts using mark.js (https://markjs.io)
                new Mark(document.getElementById("search-summary-" + key))
                    .mark(searchQuery, { "separateWordSearch": false });
            });
        }
        else
        {
            searchResults.appendChild(htmlToElement("<div><b>Nothing found</b></div>"));
        }
    });
}

function getJSON(url, fn)
{
    let xhr = new XMLHttpRequest();
    xhr.open("GET", url);
    xhr.onload = function ()
    {
        if (xhr.status === 200)
        {
            fn(JSON.parse(xhr.responseText));
        }
        else
        {
            console.error(
                "Some error processing ".concat(url, ": ", xhr.status)
                );
        }
    };
    xhr.onerror = function ()
    {
        console.error("Connection error: ".concat(xhr.status));
    };
    xhr.send();
}

// it is faster to generate an element from the raw HTML code
function htmlToElement(html)
{
    let template = document.createElement("template");
    html = html.trim();
    template.innerHTML = html;
    return template.content.firstChild;
}
