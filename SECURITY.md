# Security policy

## Supported versions

Security fixes are applied to the latest released version of Doorman on
[`main`](https://github.com/dougrathbone/2n-doorman). Older tags are not
backported unless a vulnerability is severe and still widely installed.

## Reporting a vulnerability

Doorman stores 2N HTTP API credentials in Home Assistant’s config entries and
handles directory credentials (PINs, cards, codes) in transit to the device.
Please **do not** open a public GitHub issue for security-sensitive reports.

Prefer one of:

1. [GitHub private vulnerability reporting](https://github.com/dougrathbone/2n-doorman/security/advisories/new)
   (Security → Report a vulnerability), or
2. Email the maintainer listed in `custom_components/doorman/manifest.json`
   (`codeowners`).

Include the Doorman version, Home Assistant version, and steps to reproduce.
You should receive an acknowledgement within a few days.
