FROM quay.io/centos/centos:stream9

RUN rpm --import /etc/pki/rpm-gpg/RPM-GPG-KEY-centosofficial && \
    dnf -y install vim

CMD ["vim", "--help"]
