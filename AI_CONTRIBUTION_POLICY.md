# AI Contribution Policy

## Purpose

This policy establishes guidelines for contributions to Hermeto that involve the
use of AI tools, including but not limited to Large Language Models (LLMs), code
generation assistants, and similar technologies. It aims to balance openness to
modern development workflows with the project's need for high-quality,
well-understood contributions.

## Position

Hermeto takes a **permissive approach** toward AI-assisted contributions. We
recognize AI tools as legitimate development aids that can improve productivity
and code quality when used responsibly.

This position is consistent with the [Linux Foundation's Generative AI
Policy](https://www.linuxfoundation.org/legal/generative-ai), which states that
code generated in whole or in part using AI tools can be contributed to open
source projects, **provided that licensing and intellectual property
considerations are properly addressed**.

## Contributor accountability

Regardless of AI involvement, **the contributor is fully responsible for every
aspect of their submission**. This includes correctness, security, adherence to
project standards, and licensing compliance.

Hermeto is a security-sensitive and security-oriented project: it prefetches
dependencies, validates checksums, and produces SBOMs that downstream consumers
rely on. AI-generated code that is submitted without thorough human review and
understanding poses a direct risk to its mission.

### What we expect

- **Understand your code.** You must be able to explain every line of your
    submission, defend design decisions during review, and respond to reviewer
    feedback with substance.

- **Review AI output critically.** AI tools can produce code that is
    syntactically correct, but semantically or logically flawed. Treat all AI
    output as untrusted input that requires careful validation.

- **Follow project standards.** AI-assisted contributions must meet the same
    coding standards, test coverage, and review quality as any other
    contribution. See [CONTRIBUTING.md](CONTRIBUTING.md).

### What we will reject

- **"Vibecoded" contributions.** Submissions that are purely AI-generated with
    minimal or no human review, understanding, or refinement will be rejected.
    If a contribution appears to be an unreviewed dump of AI output,
    maintainers may reject it without detailed feedback.

- **Contributions the author cannot explain.** If during code review a
    contributor is unable to demonstrate understanding of how their submission
    works, or cannot meaningfully address reviewer feedback, the contribution
    may be rejected regardless of code quality.

- **Autonomous AI agent submissions.** Pull requests should NOT be opened by
    AI agents independently. A human must be the author and submitter of every
    contribution. Contributors are also expected to engage directly with
    maintainers during code review, i.e. relaying chatbot responses to
    reviewer's comments is not acceptable.

## Disclosure requirements

Contributors **MUST** disclose AI tool usage when submitting code,
documentation, or other content to the project. Undisclosed AI usage discovered
during review may result in the contribution being rejected and a request to
re-submit with proper disclosure. Disclosure is done via [git commit
trailer](https://git-scm.com/docs/git-interpret-trailers) lines. Accepted
formats include:

```text
Assisted-by: Claude
Assisted-by: Claude Code (Claude Opus 4.6)
Co-authored-by: Claude
...
```

Note these trailers must appear in addition to the required
[`Signed-off-by`](https://developercertificate.org) trailer (DCO sign-off).

### What requires disclosure

- AI wrote significant code blocks included in the submission
- AI suggested algorithms, data structures, or architectural approaches that
  were adopted
- AI generated tests, documentation, or commit messages that were used as-is or
  with minor edits
- AI-suggested solutions that materially shaped the final implementation

### What does not require disclosure

- General Q&A or learning about a technology
- IDE autocomplete or line-level completions (e.g. basic Copilot suggestions)
- Using AI to explain existing code
- Asking AI to review human-written code
- Spell checking or minor syntax corrections
- Content that was substantially rewritten to the point where the original AI
  output is unrecognizable

## Licensing considerations

Hermeto is licensed under the [GNU General Public License v3.0](LICENSE).
**Contributors must ensure that:**

- **The terms of their AI tool do not impose restrictions on the generated
    output that conflict with the GPL-3.0 license**

- **AI-generated output does not contain copyrighted material from third parties
    that would violate the GPL-3.0 or the rights of the original authors**

- **They can legitimately provide a DCO sign-off for the contribution,
    certifying that they have the right to submit it under the project's
    license**

When in doubt about whether an AI tool's terms are compatible with GPL-3.0, err
on the side of caution and consult the tool's terms of service or reach out to
the maintainers.

## Review standards

Maintainers will evaluate all contributions — whether AI-assisted or not — on
the same criteria:

- Adherence to [coding standards](CONTRIBUTING.md#coding-standards) and
  [project guidelines](CONTRIBUTING.md#pull-request-guidelines)
- Test coverage and quality (see [test guidelines](CONTRIBUTING.md#test-guidelines))
- Security implications (especially for dependency handling and checksum
  validation)
- Long-term maintainability
- Clarity and correctness of the implementation
