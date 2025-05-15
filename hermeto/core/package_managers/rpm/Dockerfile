FROM docker.io/library/fedora:40

RUN dnf -y update && \
    dnf -y install 'dnf-command(copr)' && \
    dnf -y copr enable rpmsoftwaremanagement/manifest-plugin-testing && \
    dnf -y install 'dnf-command(manifest)'
