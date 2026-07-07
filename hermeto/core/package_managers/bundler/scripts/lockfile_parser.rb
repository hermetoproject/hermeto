#!/usr/bin/env ruby

require 'bundler'
require 'json'

lockfile_content = File.read("Gemfile.lock")
lockfile_parser = Bundler::LockfileParser.new(lockfile_content)

parsed_specs = []

lockfile_parser.specs.each do |spec|
    case spec.source
    when Bundler::Source::Rubygems
      # Requires Bundler 2.5.0+ to resolve field.
      # e.g. input:  gem_lock_name sha256=hash,...
      #      output: sha256:hash sha....
      lock_content = spec.source.checksum_store.to_lock(spec)
      pattern = /^#{Regexp.escape(spec.lock_name)} (.*)/

      checksums_raw = lock_content[pattern, 1]
      if checksums_raw == nil
        checksum = nil
      else
        checksum = checksums_raw
          .split(",")
          .map { |c| c.sub("=", ":") }
          .join(" ")                         
      end

      parsed_spec = {
        name: spec.name,
        version: spec.version,
        type: 'rubygems',
        source: spec.source.remotes.first,
        platforms: [spec.platform],
        checksums: checksum.nil? ? {} : { spec.platform => checksum }
      }

      existing_spec = parsed_specs.find { |s|
        s[:name] == parsed_spec[:name] &&
        s[:version] == parsed_spec[:version] &&
        s[:type] == 'rubygems' &&
        s[:source] == parsed_spec[:source]
      }

      if existing_spec
        # extend the platforms array and checksums dictionary
        existing_spec[:platforms] |= parsed_spec[:platforms]
        existing_spec[:checksums].merge!(parsed_spec[:checksums])
      else
        parsed_specs << parsed_spec
      end

    when Bundler::Source::Git
      parsed_spec = {
        name: spec.name,
        version: spec.version,
        type: 'git',
        url: spec.source.uri,
        ref: spec.source.revision
      }
      parsed_specs << parsed_spec

    when Bundler::Source::Path
      parsed_spec = {
        name: spec.name,
        version: spec.version,
        type: 'path',
        subpath: spec.source.path
      }
      parsed_specs << parsed_spec
    end
  end

puts JSON.pretty_generate({ bundler_version: lockfile_parser.bundler_version, dependencies: parsed_specs })

# References:
# https://github.com/ruby/rubygems/blob/master/lib/bundler/lockfile_parser.rb
