FROM docker.io/library/ruby:latest

RUN if curl -IsS www.google.com; then echo "Has network access!"; exit 1; fi

WORKDIR /app

COPY Gemfile .
COPY Gemfile.lock .
COPY tmp.gemspec .

RUN bundle install --local

CMD ["rails", "-v"]
