# Third-Party Licenses

This file records the licenses of third-party assets vendored in the Amalthea repository.
All assets are self-hosted; no CDN at runtime.

---

## HTMX

**Version:** 2.0.4
**Location:** `ui/static/vendor/htmx/htmx.min.js`
**License:** BSD-2-Clause
**Source:** https://htmx.org
**License Text:**
```
Copyright (c) 2020-present, Big Sky Software LLC
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

---

## Font Awesome Free

**Version:** 6.5.2
**Location:** `ui/static/vendor/fontawesome/`
**License:** CC BY 4.0 (icons) / SIL OFL 1.1 (fonts)
**Source:** https://fontawesome.com
**License Text (CC BY 4.0):**
```
Creative Commons Attribution 4.0 International Public License

By exercising the Licensed Rights (defined below), You accept and agree to be
bound by the terms and conditions of this Creative Commons Attribution 4.0
International Public License ("Public License"). To the extent this Public
License may be interpreted as a contract, You are accepted and agree to be
bound by the terms and conditions of this Creative Commons Attribution 4.0
International Public License ("Public License"). To the extent this Public
License may be interpreted as a contract, You are granted the Licensed Rights
in consideration of Your acceptance of these terms and conditions, and the
Licensor grants You such rights in consideration of benefits the Licensor
receives from making the Licensed Rights available under these terms and
conditions.

...

Full text: https://creativecommons.org/licenses/by/4.0/legalcode
```

**License Text (SIL OFL 1.1):**
```
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font creation
efforts of academic and linguistic communities, and to provide the free and
unrestricted use of fonts by all users.

...

Full text: https://scripts.sil.org/cms/scripts/page.php?site_id=nrsi&id=OFL
```

**Attribution (per CC BY 4.0):**
```
Icons by Font Awesome — https://fontawesome.com
Font Awesome Free 6.5.2 by @fontawesome — https://fontawesome.com
License — https://fontawesome.com/license/free (Icons: CC BY 4.0, Fonts: SIL OFL 1.1)
```

---

## Tailwind CSS

**Version:** 4.0.0
**Tool:** `scripts/tailwindcss` (standalone binary)
**License:** MIT
**Source:** https://tailwindcss.com
**License Text:**
```
MIT License

Copyright (c) 2023-present Tailwind Labs, Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## Tailwind CSS Standalone Binary

**Version:** 4.0.0
**Location:** `scripts/tailwindcss`
**License:** MIT
**Source:** https://github.com/tailwindlabs/tailwindcss/releases/tag/v4.0.0
**Note:** This is a compiled standalone binary; no Node.js runtime required.

---

## Summary Table

| Asset | Version | License | Attribution Required |
|-------|---------|---------|---------------------|
| HTMX | 2.0.4 | BSD-2-Clause | No (license in repo) |
| Font Awesome Free | 6.5.2 | CC BY 4.0 / SIL OFL 1.1 | **Yes** (see above) |
| Tailwind CSS | 4.0.0 | MIT | No (license in repo) |
| Tailwind CLI | 4.0.0 | MIT | No (license in repo) |

---

## Compliance Notes

- All assets are **self-hosted** under `ui/static/vendor/` — no CDN at runtime.
- Font Awesome attribution is satisfied by this file and the included license files.
- All licenses are permissive and compatible with Amalthea's AGPL-3.0-or-later.
- No copyleft dependencies that would impose additional obligations on downstream users.