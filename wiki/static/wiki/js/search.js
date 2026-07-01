(function () {
  function paramsQuery() {
    return new URLSearchParams(window.location.search).get("q") || "";
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, function (char) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[char];
    });
  }

  function escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function termsFor(query) {
    return query.toLowerCase().split(/\s+/).filter(Boolean);
  }

  function snippet(text, terms) {
    text = String(text || "");
    var lower = text.toLowerCase();
    var index = -1;
    for (var i = 0; i < terms.length; i += 1) {
      var termIndex = lower.indexOf(terms[i]);
      if (termIndex >= 0 && (index < 0 || termIndex < index)) {
        index = termIndex;
      }
    }
    if (index < 0) {
      return text.trim().slice(0, 180);
    }
    var start = Math.max(0, index - 70);
    var end = Math.min(text.length, index + 140);
    var prefix = start > 0 ? "..." : "";
    var suffix = end < text.length ? "..." : "";
    return prefix + text.slice(start, end).trim() + suffix;
  }

  function highlight(value, terms) {
    var escaped = escapeHtml(value);
    var usableTerms = terms.filter(function (term) { return term.length > 1; });
    if (!usableTerms.length) {
      return escaped;
    }
    var pattern = new RegExp("(" + usableTerms.map(escapeRegExp).join("|") + ")", "gi");
    return escaped.replace(pattern, "<mark>$1</mark>");
  }

  function score(document, terms, phrase) {
    var title = String(document.title || "").toLowerCase();
    var text = String(document.text || "").toLowerCase();
    var scoreValue = 0;
    if (phrase && title.indexOf(phrase) >= 0) {
      scoreValue += 100;
    }
    if (phrase && text.indexOf(phrase) >= 0) {
      scoreValue += 20;
    }
    terms.forEach(function (term) {
      if (title.indexOf(term) >= 0) {
        scoreValue += 25;
      }
      var position = text.indexOf(term);
      while (position >= 0) {
        scoreValue += 1;
        position = text.indexOf(term, position + term.length);
      }
    });
    return scoreValue;
  }

  function render(results, query) {
    var container = document.getElementById("search-results");
    var status = document.getElementById("search-status");
    if (!container) {
      return;
    }
    if (!query) {
      container.innerHTML = "";
      if (status) {
        status.textContent = "";
      }
      return;
    }
    if (!results.length) {
      container.innerHTML = "<p>No pages matched \"" + escapeHtml(query) + "\".</p>";
      if (status) {
        status.textContent = "0 results";
      }
      return;
    }
    var terms = termsFor(query);
    if (status) {
      status.textContent = results.length === 1 ? "1 result" : results.length + " results";
    }
    container.innerHTML = results.map(function (result) {
      var text = snippet(result.text, terms);
      return [
        '<div class="search_result">',
        '<h2><a href="' + escapeHtml(result.url) + '">' + highlight(result.title, terms) + "</a></h2>",
        '<p class="search_url">' + escapeHtml(result.url) + "</p>",
        '<p class="match">' + highlight(text, terms) + "</p>",
        "</div>",
      ].join("");
    }).join("");
  }

  function search(documents, query) {
    var terms = termsFor(query);
    var phrase = query.toLowerCase().trim();
    if (!terms.length) {
      return [];
    }
    return documents.map(function (document) {
      var haystack = (document.title + "\n" + document.text).toLowerCase();
      if (!terms.every(function (term) { return haystack.indexOf(term) >= 0; })) {
        return null;
      }
      return {
        document: document,
        score: score(document, terms, phrase),
      };
    }).filter(Boolean).sort(function (left, right) {
      if (right.score !== left.score) {
        return right.score - left.score;
      }
      return String(left.document.title).localeCompare(String(right.document.title));
    }).map(function (match) {
      return match.document;
    }).slice(0, 50);
  }

  function debounce(fn, delay) {
    var timer = null;
    return function () {
      var args = arguments;
      clearTimeout(timer);
      timer = setTimeout(function () {
        fn.apply(null, args);
      }, delay);
    };
  }

  function setStatus(value) {
    var status = document.getElementById("search-status");
    if (status) {
      status.textContent = value;
    }
  }

  function updateUrl(query) {
    var url = new URL(window.location.href);
    if (query) {
      url.searchParams.set("q", query);
    } else {
      url.searchParams.delete("q");
    }
    window.history.replaceState(null, "", url.toString());
  }

  function bindSearch(documents, form, input) {
    function run(nextQuery, replaceUrl) {
      if (replaceUrl) {
        updateUrl(nextQuery);
      }
      render(search(documents, nextQuery), nextQuery);
    }

    if (form && input) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        run(input.value.trim(), true);
      });
      input.addEventListener("input", debounce(function () {
        run(input.value.trim(), true);
      }, 120));
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var input = document.getElementById("search-page-input");
    var form = document.getElementById("search-page-form");
    var query = paramsQuery();
    if (input) {
      input.value = query;
    }
    setStatus("Loading search index...");
    fetch(window.GititPySearchIndex)
      .then(function (response) { return response.json(); })
      .then(function (documents) {
        render(search(documents, query), query);
        bindSearch(documents, form, input);
      })
      .catch(function () {
        setStatus("Search index could not be loaded.");
      });
  });
})();
