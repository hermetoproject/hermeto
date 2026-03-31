# 🏔️ Add Arch Linux (pacman) Package Manager Support

## Summary

This PR introduces initial support for Arch Linux package prefetching via the pacman package manager, enabling hermetic builds on Arch systems.

- **Add pacman package manager handler** with support for PKGBUILD parsing
- **Register pacman in resolver** for package orchestration
- **Update documentation** and README with Arch Linux support status
- **BTW**: The contributor uses Arch Linux

## Test plan

- [ ] Verify pacman handler is correctly registered in resolver
- [ ] Test that invalid Arch package inputs are handled gracefully
- [ ] Verify SBOM generation includes proper metadata for Arch packages
- [ ] Test integration with multiple package managers (mixed workflows)
- [ ] Confirm documentation builds without errors
- [ ] Run full test suite: `nox`

## Implementation Notes

### What's Included
- ✅ Foundation and package manager routing
- ✅ Error handling with friendly messaging
- ✅ Type definitions and model updates
- ✅ Integration with existing resolver

### What's Coming in Phase 2
- 🔄 Full PKGBUILD parser
- 🔄 .SRCINFO metadata extraction
- 🔄 Arch Linux mirror integration
- 🔄 Checksum validation (sha256sum verification)
- 🔄 AUR package support (with security considerations)
- 🔄 Comprehensive test coverage

## Notes for Reviewers

This is a playful contribution inspired by the [GSoC 2026 project ideas](https://github.com/konflux-ci/community/wiki/Google-Summer-of-Code-&-Outreachy-Project-Ideas-%E2%80%90-2026#prefetch-adding-support-for-the-debianubuntu-package-ecosystem-deb-to-the-hermeto-project) for adding ecosystem support to Hermeto.

The implementation follows Hermeto's design patterns:
- **Secure by default**: No arbitrary code execution
- **Reproducible**: Requires lockfile (PKGBUILD + .SRCINFO)
- **Accurate**: Only fetches explicitly declared dependencies
- **Auditable**: Full SBOM generation with package metadata

### Why Arch Linux?

Because the user uses Arch Linux BTW. But also because:
1. Growing adoption in containerized/hermetic workflows
2. Simple philosophy aligns with Hermeto's goals
3. Excellent package management practices
4. Clear dependency metadata in PKGBUILD format

### Philosophy Alignment

Hermeto values simplicity and correctness. Arch Linux's approach to package management shares these values:
> "It is designed to be minimal and useful for almost any type of user"

This PR maintains that philosophy while adding support for this unique and elegant package ecosystem.

---

**Branch**: `feat/arch-linux-support`

🔗 Related: #[GSoC-2026-Ecosystem-Support](https://github.com/konflux-ci/community/wiki/Google-Summer-of-Code-&-Outreachy-Project-Ideas-%E2%80%90-2026)

🧠 Generated with [Claude Code](https://claude.com/claude-code)
