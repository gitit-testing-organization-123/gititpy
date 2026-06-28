(function () {
  function paramsQuery() {
    return new URLSearchParams(window.location.search).get("q") || "";
  }

  function escapeHtml(value) {
    return value.replace(/[&<>"']/g, function (char) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[char];
    });
  }

  function snippet(text, query) {
    var lower = text.toLowerCase();
    var index = lower.indexOf(query.toLowerCase());
    if (index < 0) {
      return text.trim().slice(0, 180);
    }
    var start = Math.max(0, index - 70);
    var end = Math.min(text.length, index + query.length + 110);
    return text.slice(start, end).trim();
  }

  function render(results, query) {
    var container = document.getElementById("search-results");
    if (!container) {
      return;
    }
    if (!query) {
      container.innerHTML = "";
      return;
    }
    if (!results.length) {
      container.innerHTML = "<p>No pages matched \"" + escapeHtml(query) + "\".</p>";
      return;
    }
    container.innerHTML = results.map(function (result) {
      return [
        '<div class="search_result">',
        '<h2><a href="' + result.url + '">' + escapeHtml(result.title) + "</a></h2>",
        '<p class="match">' + escapeHtml(snippet(result.text, query)) + "</p>",
        "</div>",
      ].join("");
    }).join("");
  }

  function search(documents, query) {
    var terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) {
      return [];
    }
    return documents.filter(function (document) {
      var haystack = (document.title + "\n" + document.text).toLowerCase();
      return terms.every(function (term) {
        return haystack.indexOf(term) >= 0;
      });
    }).slice(0, 50);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var input = document.getElementById("search-page-input");
    var form = document.getElementById("search-page-form");
    var query = paramsQuery();
    if (input) {
      input.value = query;
    }
    fetch(window.GititPySearchIndex)
      .then(function (response) { return response.json(); })
      .then(function (documents) {
        render(search(documents, query), query);
        if (form && input) {
          form.addEventListener("submit", function (event) {
            event.preventDefault();
            var nextQuery = input.value.trim();
            var url = new URL(window.location.href);
            if (nextQuery) {
              url.searchParams.set("q", nextQuery);
            } else {
              url.searchParams.delete("q");
            }
            window.history.replaceState(null, "", url.toString());
            render(search(documents, nextQuery), nextQuery);
          });
        }
      });
  });
})();
