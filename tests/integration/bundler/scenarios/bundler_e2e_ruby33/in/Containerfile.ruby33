FROM mirror.gcr.io/ruby:3.3 AS builder

# Test disabled network access
RUN if getent hosts www.google.com; then echo "Has network access!"; exit 1; fi

WORKDIR /src
ENV BUNDLE_PATH=/gems

RUN . /tmp/hermeto.env && bundle install

FROM mirror.gcr.io/ruby:3.3
COPY --from=builder /gems /gems
RUN test -d /gems/ruby/*/bundler/gems/json-schema-*
