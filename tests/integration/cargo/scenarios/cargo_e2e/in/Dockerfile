FROM mirror.gcr.io/library/rust:1.97.1-slim-bookworm as builder

COPY . /source

WORKDIR /source

RUN cargo build --release

FROM registry.fedoraproject.org/fedora-minimal:39

COPY --from=builder /source/target/release/rustic /bin/rustic

ENTRYPOINT ["rustic"]
