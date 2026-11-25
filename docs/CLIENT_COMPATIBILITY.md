# Mail client compatibility

**Status:** Automated format-level regression tests; real-client acceptance tests remain open.

OpenSigWeave is a server-side signature injector. SOGo, Outlook and Thunderbird are
mail clients/webmail composers, not transport adapters. Their generated MIME and
reply markup are therefore tested at the adapter placement boundary; mail-server
integration remains a separate concern.

## Initial compatibility targets

| Client | Plain-text reply format | HTML reply format | Automated coverage |
|---|---|---|---|
| SOGo | Quoted lines prefixed with `>` | Structural `blockquote` quote | Stalwart multipart/alternative fixture with SOGo User-Agent and OpenGroupware MIME boundary |
| Microsoft Outlook | German `Von/Gesendet/An` and English `From/Sent/To` headers | `#divRplyFwdMsg` | Placement and Stalwart MIME tests |
| Mozilla Thunderbird | `On … wrote:` and quoted lines | `.moz-cite-prefix` / `blockquote type="cite"` | Placement tests |

The SOGo fixture verifies both MIME alternatives in one multipart message. It
does not claim that every SOGo release or every user-configured quoting mode
emits identical markup. Likewise, Outlook and Thunderbird have multiple
versions, settings and message-format paths.

## Required behavior

1. Place the signature after the user's newly written text and before recognised
   quoted history.
2. Apply the same placement contract to `text/plain` and `text/html`.
3. Preserve multipart/alternative structure and unrelated MIME parts.
4. Do not modify signed or encrypted message structures.
5. Do not inject twice when the OpenSigWeave idempotency marker is present.
6. When no known quote boundary is detected, use the documented safe end-of-body
   fallback instead of guessing.

## Acceptance status

CI tests use representative message fixtures. They are not substitutes for
manual end-to-end acceptance with real SOGo, Outlook and Thunderbird clients.
Before declaring a client version production-certified, capture a real reply and
forward from that version, add the sanitized MIME fixture to the regression
suite, and verify the delivered message and DKIM result through an isolated
mail-server test.
