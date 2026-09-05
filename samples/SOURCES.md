# Sample images — sources & licenses

These faces are used only to demonstrate and test the pipeline. All are
**public domain** works of U.S. federal government employees (official White
House photographs), retrieved from Wikimedia Commons. No scraped photos of
private third parties are included, per the project's biometric rules.

| File | Subject | Role in the project | Source (Wikimedia Commons) | License |
|------|---------|--------------------|-----------------------------|---------|
| `obama_a.jpg` | Barack Obama | Demo subject; enrollment reference | File:President Barack Obama.jpg | Public domain (US Gov work) |
| `obama_b.jpg` | Barack Obama | Same-person match test / second angle | File:Barack Obama on phone with Benjamin Netanyahu 2009-06-08.jpg | Public domain (US Gov work) |
| `biden.jpg`   | Joe Biden   | Different-person (negative) test | File:Joe Biden presidential portrait.jpg | Public domain (US Gov work) |

Measured with the face stage (`buffalo_l` / ArcFace):
- `obama_a` vs `obama_b` cosine similarity ≈ **0.73** (same person)
- `obama_a` vs `biden`   cosine similarity ≈ **−0.06** (different person)

> The consent framing treats the subject as "yourself." For a runnable demo we
> use a well-known public-domain face as a stand-in subject — this also makes the
> Phase-3 web search meaningful, since such a face is genuinely indexed across
> the public web.
