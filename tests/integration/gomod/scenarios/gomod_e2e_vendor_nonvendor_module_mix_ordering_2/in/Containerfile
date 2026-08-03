FROM mirror.gcr.io/golang:1.23-alpine AS builder

# Test disabled network access
RUN if getent hosts www.google.com; then echo "Has network access!"; exit 1; fi

WORKDIR /src
RUN . /tmp/hermeto.env && \
    cd vendored-module && go build -o /usr/local/bin/vendored-module && \
    cd ../non-vendored-module && go build -o /usr/local/bin/non-vendored-module

FROM scratch

COPY --from=builder /usr/local/bin/vendored-module /
COPY --from=builder /usr/local/bin/non-vendored-module /

CMD ["/non-vendored-module"]
