# Microsoft Marketplace listing — Aegis Legal CLM for Word

Copy-paste starting point for the Partner Center submission (Marketplace listings
page). Fill in every `[bracketed]` placeholder before submitting — see
`word-addin/README.md` → "Production hosting" and the privacy/support pages in
`public/` for the related items this listing links to.

## App name

```
Aegis Legal CLM
```

Must match (or be very close to) `ProviderName`/`DisplayName` in `manifest.xml`
and the Publisher name on your Partner Center account.

## Short description (search results / card — keep to one line)

```
AI contract review, risk scoring, and redlining from Aegis Legal CLM, without leaving Word.
```

## Long description (HTML-formatted, per Partner Center's editor)

```html
<p>Aegis Legal CLM brings your organization's contract intelligence into Microsoft Word.
Review a contract, get a weighted risk score, compare it against your team's playbooks,
and ask questions — all without switching to a browser tab.</p>

<ul>
  <li><strong>Review</strong> — a real, explainable risk score and open playbook deviations
  for the document you have open, powered by the same engine as the Aegis web app.</li>
  <li><strong>Ask</strong> — a streaming, multi-turn conversation about the contract, with
  the assistant reading the document and using tools to answer precisely.</li>
  <li><strong>Explain selection</strong> — highlight any clause and ask about just that text,
  no typing required.</li>
  <li><strong>Playbooks</strong> — run your organization's playbook against the contract, or
  browse its clause library and insert standard language directly into the document.</li>
  <li><strong>Redlines that respect Track Changes</strong> — every suggested edit is inserted
  as a tracked change, so a reviewer can accept or reject it in Word as normal.</li>
  <li><strong>Stays in sync</strong> — link the open document to a contract record once; every
  action (redlines, versions, risk) reads from and writes back to the same Aegis account your
  team already uses.</li>
</ul>

<p><strong>Requires an active Aegis Legal CLM account.</strong> [Add a line here if your
organization only offers this to existing customers / via invitation — see the FAQ on
"If my app targets enterprises" for how that changes the submission requirements.]</p>
```

## Category

Office Add-ins are typically listed under **Productivity** and/or **Business** in
Partner Center's category picker — pick whichever specific options are available
at submission time; there may not be a dedicated "Legal" category. Choose up to
3; don't force a fit if only 1 applies.

## Search keywords (optional)

```
contract review, redlining, legal, risk score, playbook, CLM, contract management, legal AI
```

## Screenshots (you'll need to capture these yourself in Word — I can't drive Word directly)

Microsoft requires at least one; a few that show the actual range of what it does will
serve the listing better than just the sign-in screen. Suggested shots, in this order:

1. **The Review panel** with a real contract open — risk-level hero (score + band) and at
   least one severity-colored deviation card visible.
2. **A streamed Ask Aegis answer** mid-conversation — shows the chat-style interaction and
   the "Reading the contract…" tool-status row if you can time the screenshot right.
3. **The Playbook chip's two-choice screen** ("Run this playbook" / "Browse its clause
   library") or the clause-library list itself with an "Insert clause" button visible.
4. **The contract panel** — version history, the "Save as new version" counterparty-revision
   checkbox, and a proposed-redline card with Accept/Reject.
5. **Dark mode** of any one of the above, to show the theme toggle exists.

Crop to just the Word window (or just the task pane) at a reasonable resolution;
Microsoft's own screenshot guidance is linked from the checklist as "craft effective
AppSource store images."

## Notes for certification (paste into Partner Center's "Notes for certification" box)

This add-in uses email/password, not Microsoft Entra ID/SSO, so a **working test
account is mandatory** or the submission auto-fails. Template:

```
This add-in requires an Aegis Legal CLM account to sign in.

Test account:
  Email: [test-reviewer@yourdomain.example]
  Password: [set a real password for this account before submitting]

To test:
1. Open the add-in in Word (Insert > Add-ins > Aegis Legal CLM).
2. Sign in with the test account above.
3. Open or paste in a sample contract (a short NDA or services agreement works well).
4. Click "Review" to see a risk score and any playbook deviations.
5. Type a question in the command bar (e.g. "What's the termination notice period?") to see
   a streamed answer.
6. Select a sentence in the document and click "Explain selection" to test the
   selection-aware flow.

[If you seed a specific sample contract for this test account so reviewers always
see representative Review/Playbook output, mention that here.]
```

Do **not** put this test account's credentials anywhere else public (this file, once
filled in with a real password, shouldn't be committed to a public repo — keep the
real values only in the Partner Center submission form itself).
