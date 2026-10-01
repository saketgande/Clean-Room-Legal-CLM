# The document editor, with the fonts a contract is actually set in.
#
# The stock image has no Calibri or Cambria — Microsoft's, and not
# redistributable — so every document written in them was drawn in DejaVu Sans,
# which is wider: the page looked wrong and lines wrapped where they should not.
# Carlito and Caladea are metric-compatible replacements (same widths, same
# line breaks) and are free to ship. Installed here rather than in a running
# container, which loses them the moment it is recreated.
FROM onlyoffice/documentserver:latest

RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-crosextra-carlito fonts-crosextra-caladea \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f
