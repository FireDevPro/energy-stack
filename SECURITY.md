# Security policy

## Reporting a vulnerability

If you believe you have found a security vulnerability in this
repository, please report it privately rather than opening a public
issue.

**Preferred channel:** GitHub's private vulnerability reporting feature.
On the repository page, click the **Security** tab, then **Report a
vulnerability**. This creates a private security advisory visible only
to the repository maintainer.

If GitHub private reporting is not available to you, email the
maintainer at the address listed in [`CITATION.cff`](CITATION.cff).
Use a clear subject line indicating a security report (for example,
`SECURITY: <short description>`).

## What to include

- A short description of the issue.
- The version, commit SHA, or branch you observed it on.
- Reproduction steps. Where possible, include exact commands and
  expected vs observed behavior.
- The impact you believe the issue has.
- Any suggested mitigation, if you have one.

## What to expect

This is a solo-maintained research / home-lab project. Acknowledgement
target is within seven calendar days; substantive response and triage
within thirty. Critical fixes will be prioritized; lower-severity
issues may be batched.

The maintainer will coordinate disclosure timing with you before any
public discussion of the issue.

## Scope

- The current `main` branch of this repository.
- Released tagged versions matching the pattern `osf-prereg-*` or any
  future `v*` release tag.

Out of scope:

- Issues in third-party dependencies (please report those upstream;
  GitHub Dependabot alerts in this repo are tracked separately and do
  not need duplicate reports).
- Pi-lab runtime infrastructure (network, host OS, Docker daemon)
  outside the repository's scope of control.
- Pre-rewrite git history. The repository was sanitized and recreated
  prior to becoming public; historical commits visible in any prior
  private snapshot are out of scope for the public-facing repository.

## Acknowledgements

Reporters who identify valid issues will be credited in the
release notes or security advisory unless they request to remain
anonymous.
