#!/usr/bin/env ruby

require 'bundler'
require 'json'

lockfile_content = File.read("Gemfile.lock")
lockfile_parser = Bundler::LockfileParser.new(lockfile_content)

parsed_specs = []

lockfile_parser.specs.each do |spec|
    parsed_spec = {
      name: spec.name,
      version: spec.version
    }

    case spec.source
    when Bundler::Source::Rubygems
      parsed_spec[:type] = 'rubygems'
      parsed_spec[:source] = spec.source.remotes.first
      parsed_spec[:platforms] = [spec.platform]
    when Bundler::Source::Git
      parsed_spec[:type] = 'git'
      parsed_spec[:url] = spec.source.uri
      parsed_spec[:branch] = spec.source.branch
      parsed_spec[:ref] = spec.source.revision
    when Bundler::Source::Path
      parsed_spec[:type] = 'path'
      parsed_spec[:subpath] = spec.source.path
    end

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

  end

puts JSON.pretty_generate({ bundler_version: lockfile_parser.bundler_version, dependencies: parsed_specs })

# References:
# https://github.com/rubygems/rubygems/blob/master/bundler/lib/bundler/lockfile_parser.rb
