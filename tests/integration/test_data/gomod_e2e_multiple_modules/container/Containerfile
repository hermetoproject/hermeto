FROM mirror.gcr.io/golang:1.18.10-alpine

# Test disabled network access
RUN if getent hosts www.google.com; then echo "Has network access!"; exit 1; fi

WORKDIR /src
RUN . /tmp/hermeto.env && \
    cd spam-module && go build -o /usr/bin/spam-module && \
    cd ../eggs-module && go build -o /usr/bin/eggs-module

# Check that correct versions of rsc.io/quote were used
CMD echo "spam-module: expecting rsc.io/quote v1.5.2" && \
    go version -m /usr/bin/spam-module && \
    go version -m /usr/bin/spam-module | grep 'rsc.io/quote\s*v1.5.2' && \
    echo "eggs-module: expecting rsc.io/quote v1.5.1" && \
    go version -m /usr/bin/eggs-module && \
    go version -m /usr/bin/eggs-module | grep 'rsc.io/quote\s*v1.5.1'
