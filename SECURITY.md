# Security Policy

**Maintainers work closely with security experts from [our CRA steward
partner](#eu-cyber-resilience-act--open-source-steward-statement) to apply
security best practices when handling vulnerabilities and responding to
incidents.**

Thank you for helping us maintain a secure project!

## Secure Development Practices

We use the following practices to help prevent security problems:

- AI-assisted code review alongside human review of code changes.
- Automated unit and integration tests, linting, and type checking.
- CodeQL scans to identify potential security issues.
- Renovate pull requests to keep dependencies and build tooling up to date.

## Supported Versions

Only the latest stable release receives security fixes. Support for each
release ends when the next stable release is published. Users should upgrade.

https://github.com/hermetoproject/hermeto/releases/latest

## Reporting a Vulnerability or Incident

Report suspected vulnerabilities and security incidents privately:

https://github.com/hermetoproject/hermeto/security/advisories/new

**Please do NOT share sensitive details in public issues or pull requests.**

Report concerns even if you are unsure they are security issues.
To help us assess and address the concern, include what you know:

- A concise summary of the concern.
- A technical description, including affected versions or systems.
- Steps to reproduce the issue, or evidence of an incident or ongoing attack.
- The possible impact or exploit scenarios, if known.
- **DO NOT** include any suggested fixes for the reported issue in the report
  text itself.

Reports are handled privately through GitHub Security Advisories. Maintainers
may invite more collaborators to help investigate and resolve the issue.

We aim to acknowledge reports and provide an initial assessment or next steps
within **10 business days**. Fixing an issue may take longer. We keep reporters
informed of progress.

Ongoing attacks and suspected compromise receive urgent attention as soon as
maintainers become aware of them.

## Vulnerability Management

Maintainers handle reports as follows:

1. **Assess:** Analyse the report, identify affected versions, and determine
   how affected code or dependencies are used. Set priorities based on
   potential harm, exposure to attacks, how easily the issue can be exploited,
   and whether exploitation is already happening.
2. **Fix:** Develop fixes privately for vulnerabilities that are not public.
   Review and test fixes against the reported issue and check for regressions.
   Work with other affected projects when needed. Release urgent fixes
   outside the regular schedule.
3. **Publish:** Coordinate disclosure timing with the reporter and the CRA
   steward's security experts. Publish an advisory describing affected versions,
   impact, fixes, and/or mitigations. Request a CVE where appropriate.
4. **Follow up:** Explain the outcome to the reporter and track any remaining
   non-critical work.

We publish security advisories at:
https://github.com/hermetoproject/hermeto/security/advisories

Release notes link to the relevant advisories.

## Incident Response

Security incidents include suspected compromise of project accounts,
repositories, build or release workflows, publishing credentials, or
released artifacts.

Maintainers work closely with the CRA steward to contain the incident,
assess its impact, remediate the issue, verify affected systems and releases,
communicate with users as appropriate, and review how to prevent recurrence.

## EU Cyber Resilience Act — Open Source Steward Statement

This project is stewarded by **Red Hat, Inc.**, an open-source software steward as defined in Article 3(14) of the [EU Cyber Resilience Act (Regulation 2024/2847)](https://eur-lex.europa.eu/eli/reg/2024/2847/oj/eng).
Contact: [cra-steward@redhat.com](mailto:cra-steward@redhat.com)

Refer to [Red Hat's security practices and vulnerability management policy](https://access.redhat.com/security/) for detailed information.

Full details of Red Hat’s security disclosure and remediation process can be found here: <https://access.redhat.com/articles/red-hat-coordinated-vulnerability-disclosure>
