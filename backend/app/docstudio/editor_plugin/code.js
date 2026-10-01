// The page beside the editor leaves the words to look for on the server; this
// asks for them every second — for the document this editor holds, by its
// version — and selects them. Polling, not a socket: a second's wait is not
// felt, and there is nothing to keep alive.
(function () {
  // The dev tool's own address, since a plugin inside the editor is a static
  // file with nothing to template it. The editor's config carries both this and
  // the version, and they are used as soon as they arrive.
  var base = "http://localhost:8000/api/v1/docstudio/dev/editor/plugin/";
  var key = "";     // the version this editor holds; words are asked for by it
  var seen = 0;

  var tell = function (what) { fetch(base + "log?m=" + encodeURIComponent(what)); };

  // The editor searches inside a paragraph, and a clause quoted from a contract
  // often runs across several. So the whole quote is tried first, then its
  // opening words: the citation lands on its clause either way.
  function find(text) {
    var words = text.split(/\s+/).filter(Boolean);
    var lengths = [text];
    if (words.length > 12) lengths.push(words.slice(0, 12).join(" "));
    if (words.length > 6) lengths.push(words.slice(0, 6).join(" "));
    if (words.length > 3) lengths.push(words.slice(0, 3).join(" "));
    var tries = [];
    for (var i = 0; i < lengths.length; i++) {
      var said = lengths[i];
      // The editor matches letter for letter, and a contract's apostrophes and
      // dashes are typographic where a quotation of it often is not.
      var plain = said.replace(/[\u2018\u2019]/g, "'").replace(/[\u201c\u201d]/g, '"')
                      .replace(/[\u2013\u2014]/g, "-");
      var typed = said.replace(/'/g, "\u2019").replace(/"/g, "\u201d");
      tries.push(said);
      if (plain !== said) tries.push(plain);
      if (typed !== said) tries.push(typed);
    }
    look(tries, 0);
  }

  function look(tries, which) {
    if (which >= tries.length) {
      tell("found nothing of: " + tries[0].slice(0, 60));
      return;
    }
    window.Asc.scope.wanted = tries[which];
    window.Asc.plugin.callCommand(function () {
      // Api is the editor's own document API, inside the editor.
      var found = Api.GetDocument().Search(Asc.scope.wanted, false);
      if (found && found.length) found[0].Select();
      return found ? found.length : 0;
    }, false, false, function (n) {
      if (Number(n) > 0) tell("found " + n + " of: " + tries[which].slice(0, 60));
      else look(tries, which + 1);
    });
  }

  // Accepting and rejecting where the person can see it happen, which is what
  // the editor's own review pane does. The alternative — changing the file on
  // the server — means closing the editor and opening it again.
  function review(what) {
    window.Asc.scope.what = what;
    window.Asc.plugin.callCommand(function () {
      var document_ = Api.GetDocument();
      var left = document_.GetReviewChanges ? document_.GetReviewChanges().length : -1;
      if (Asc.scope.what === "accept") document_.AcceptAllRevisionChanges();
      else document_.RejectAllRevisionChanges();
      return left;
    }, false, false, function (n) { tell(window.Asc.scope.what + "ed " + n + " tracked change(s)"); });
  }

  function ask() {
    fetch(base + "command?key=" + encodeURIComponent(key) + "&since=" + seen)
      .then(function (r) { return r.json(); })
      .then(function (c) {
        if (c.id <= seen) return;
        seen = c.id;
        if (c.action === "accept" || c.action === "reject") review(c.action);
        else if (c.text) find(c.text);
      })
      .catch(function () { /* the page beside it is closed or reloading */ })
      .then(function () { setTimeout(ask, 1000); });
  }

  window.Asc.plugin.init = function () {
    var options = ((window.Asc.plugin.info || {}).options) || {};
    if (options.base) base = options.base;
    if (options.key) key = options.key;
    var info = window.Asc.plugin.info || {};
    tell("started mode=" + info.mode + " editorType=" + info.editorType +
         " review=" + JSON.stringify(info.review || null) +
         " canEdit=" + (info.permissions ? info.permissions.edit : "?") +
         " restrictions=" + info.restrictions);
    ask();
  };
})();
