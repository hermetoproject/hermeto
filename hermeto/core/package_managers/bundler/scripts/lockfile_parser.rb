#!/usr/bin/env ruby

require 'bundler'
require 'json'

class HermetoLockfileParser < Bundler::LockfileParser
  attr_reader :original_platform

  def initialize(lockfile)
    @original_platform = {}
    super
  end

  private

  def parse_spec(line)
    super

    return unless line =~ NAME_VERSION
    if @current_spec && $1.size == 4
      @original_platform[@current_spec] = $4 || Gem::Platform::RUBY
    end
  end
end

lockfile_content = File.read("Gemfile.lock")
lockfile_parser = HermetoLockfileParser.new(lockfile_content)

parsed_specs = []

lockfile_parser.specs.each do |spec|
    case spec.source
    when Bundler::Source::Rubygems
      parsed_spec = {
        name: spec.name,
        version: spec.version,
        type: 'rubygems',
        source: spec.source.remotes.first,
        platforms: [spec.platform],
        original_platforms: [lockfile_parser.original_platform[spec]]
      }

      existing_spec = parsed_specs.find { |s|
        s[:name] == parsed_spec[:name] &&
        s[:version] == parsed_spec[:version] &&
        s[:type] == 'rubygems' &&
        s[:source] == parsed_spec[:source]
      }

      if existing_spec
        # extend the platforms arrays
        existing_spec[:platforms] << parsed_spec[:platforms].first
        existing_spec[:original_platforms] << parsed_spec[:original_platforms].first
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
