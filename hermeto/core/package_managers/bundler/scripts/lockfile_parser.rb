#!/usr/bin/env ruby

require 'bundler'
require 'json'

lockfile_content = File.read("Gemfile.lock")
lockfile_parser = Bundler::LockfileParser.new(lockfile_content)

parsed_specs = []

lockfile_parser.specs.each do |spec|
    case spec.source
    when Bundler::Source::Rubygems
      parsed_spec = {
        name: spec.name,
        version: spec.version,
        type: 'rubygems',
        source: spec.source.remotes.first,
        platforms: [spec.platform]
      }

      existing_spec = parsed_specs.find { |s|
        s[:name] == parsed_spec[:name] &&
        s[:version] == parsed_spec[:version] &&
        s[:type] == 'rubygems' &&
        s[:source] == parsed_spec[:source]
      }

      if existing_spec
        # extend the platforms array
        existing_spec[:platforms] << parsed_spec[:platforms].first
      else
        parsed_specs << parsed_spec
      end

    when Bundler::Source::Git
      parsed_spec = {
        name: spec.name,
        version: spec.version,
        type: 'git',
        url: spec.source.uri,
        branch: spec.source.branch,
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
# https://github.com/rubygems/rubygems/blob/master/bundler/lib/bundler/lockfile_parser.rb
