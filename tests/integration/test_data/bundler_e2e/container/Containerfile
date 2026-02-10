FROM mirror.gcr.io/ruby:3.3

# Test disabled network access
RUN if getent hosts www.google.com; then echo "Has network access!"; exit 1; fi

WORKDIR /src

RUN . /tmp/hermeto.env && bundle install
