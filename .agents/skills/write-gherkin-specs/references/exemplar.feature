Feature: pnpm lockfile version support

  Hermeto parses pre-resolved pnpm lockfiles. It must accept the lockfile
  formats emitted by supported pnpm releases, reject the ones it cannot parse
  with an actionable message, and preserve the lockfile's structure when it
  rewrites it for the hermetic build.

  Scenario: A single-document lockfile is accepted
    Given a project with a pnpm lockfile declaring lockfileVersion 9
    When hermeto fetches the pnpm dependencies
    Then the fetch succeeds
    And the SBOM lists every package declared in the lockfile

  Scenario: An unsupported lockfile version is rejected with guidance
    Given a project with a pnpm lockfile declaring lockfileVersion 7
    When hermeto fetches the pnpm dependencies
    Then the fetch fails with an unsupported-feature error
    And the error names the lockfile versions hermeto does support

  Scenario: A lockfile with no version field is rejected
    Given a project with a pnpm lockfile that has no lockfileVersion field
    When hermeto fetches the pnpm dependencies
    Then the fetch fails with an invalid-lockfile-format error
    And the error tells the user the lockfileVersion field is required

  Scenario Outline: Supported lockfile versions across pnpm releases
    Given a project with a pnpm lockfile declaring lockfileVersion <version>
    When hermeto fetches the pnpm dependencies
    Then the fetch <outcome>

    Examples:
      | version | outcome   |
      | 9       | succeeds  |
      | 9.0     | succeeds  |
      | 8       | fails     |
      | 7       | fails     |

  Scenario: The rewritten lockfile keeps the structure hermeto was given
    Given a project with a pnpm lockfile declaring lockfileVersion 9
    When hermeto fetches the pnpm dependencies
    Then the rewritten lockfile has the same document structure as the original
    And every registry package resolves to a local tarball
