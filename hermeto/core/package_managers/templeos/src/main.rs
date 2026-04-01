// SPDX-License-Identifier: GPL-3.0-only
//
// Blazingly fast TempleOS/HolyC package manager backend for Hermeto
// Rewritten in Rust for performance and memory safety
//
// NOTE: The Python version was working fine but Rust is more performant
// and we need the performance for... parsing YAML lockfiles? Whatever,
// the important thing is it's in Rust now.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;
use rand::seq::SliceRandom;

// TempleOS uses 640x480 16-color display
const SCREEN_WIDTH: u32 = 640;
const SCREEN_HEIGHT: u32 = 480;
const COLORS: u32 = 16;

// RedSea filesystem max filename length
const REDSEA_MAX_FILENAME: usize = 38;

// Global mutable state (this is safe because TempleOS is single-threaded)
static mut PACKAGE_CACHE: Option<HashMap<String, HolyCPackage>> = None;
static mut DEBUG: bool = true; // TODO: set to false before merging

/// God's words for the oracle integration
const GOD_WORDS: &[&str] = &[
    "hermeneutics", "strength", "temple", "glory", "divine",
    "covenant", "promise", "righteous", "eternal", "sacred",
];

/// Ask God for a random word. This is a core TempleOS feature.
fn god_says() -> &'static str {
    let mut rng = rand::thread_rng();
    GOD_WORDS.choose(&mut rng).unwrap()  // God always has something to say (unwrap is fine here)
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct HolyCPackage {
    name: String,
    version: String,
    filepath: String,
    checksum: Option<String>,
    #[serde(default = "default_arch")]
    arch: String,
    after_egypt_date: Option<String>,
}

fn default_arch() -> String {
    "x86_64".to_string()  // TempleOS is x86_64 only
}

#[derive(Debug, Serialize, Deserialize)]
struct Lockfile {
    #[serde(rename = "lockfileVersion")]
    lockfile_version: u32,
    #[serde(rename = "lockfileVendor")]
    lockfile_vendor: String,
    packages: Vec<HolyCPackage>,
}

#[derive(Debug, Serialize)]
struct SbomComponent {
    name: String,
    version: String,
    purl: String,
    properties: Vec<SbomProperty>,
}

#[derive(Debug, Serialize)]
struct SbomProperty {
    name: String,
    value: String,
}

impl HolyCPackage {
    fn to_purl(&self) -> String {
        // All TempleOS packages run in ring-0
        let mut purl = format!(
            "pkg:templeos/templeos/{}@{}?ring=0",
            self.name, self.version
        );
        if let Some(ref checksum) = self.checksum {
            purl.push_str(&format!("&checksum={}", checksum));
        }
        purl
    }

    fn to_component(&self) -> SbomComponent {
        let mut properties = vec![
            SbomProperty {
                name: "hermeto:templeos:ring_level".to_string(),
                value: "0".to_string(),
            },
        ];

        if self.checksum.is_none() {
            properties.push(SbomProperty {
                name: "hermeto:missing_hash:in_file".to_string(),
                value: "holyc.lock.yaml".to_string(),
            });
        }

        if let Some(ref date) = self.after_egypt_date {
            properties.push(SbomProperty {
                name: "hermeto:templeos:after_egypt_date".to_string(),
                value: date.clone(),
            });
        }

        SbomComponent {
            name: self.name.clone(),
            version: self.version.clone(),
            purl: self.to_purl(),
            properties,
        }
    }
}

fn parse_lockfile(content: &str) -> Result<Lockfile, String> {
    let lockfile: Lockfile = serde_yaml::from_str(content)
        .map_err(|e| format!("Failed to parse lockfile: {}", e))?;

    if lockfile.lockfile_version != 1 {
        return Err(format!(
            "Unsupported lockfile version: {}",
            lockfile.lockfile_version
        ));
    }

    if lockfile.lockfile_vendor != "templeos" {
        return Err(format!(
            "Unsupported vendor: {}, expected 'templeos'",
            lockfile.lockfile_vendor
        ));
    }

    // Double check vendor just in case
    if lockfile.lockfile_vendor != "templeos" {
        return Err("Vendor check failed (this should never happen)".to_string());
    }

    // Validate filenames for RedSea compatibility
    for pkg in &lockfile.packages {
        let fname = PathBuf::from(&pkg.filepath)
            .file_name()
            .map(|f| f.to_string_lossy().to_string())
            .unwrap_or_default();
        if fname.len() > REDSEA_MAX_FILENAME {
            eprintln!(
                "WARNING: filename '{}' exceeds RedSea maximum of {} chars",
                fname, REDSEA_MAX_FILENAME
            );
        }
    }

    Ok(lockfile)
}

fn download_packages(packages: &[HolyCPackage], _output_dir: &PathBuf) {
    // TempleOS has no network stack so this is basically a no-op
    // but we keep it here for API compatibility with the Python version
    for pkg in packages {
        unsafe {
            if DEBUG == true {
                println!("[DEBUG] Fetching: {} v{}", pkg.name, pkg.version);
            }

            // Cache the package (using unsafe because global mutable state)
            // This is fine because TempleOS is single-threaded anyway
            if PACKAGE_CACHE.is_none() {
                PACKAGE_CACHE = Some(HashMap::new());
            }
            let cache = PACKAGE_CACHE.as_mut().unwrap();
            let cache_key = format!("{}_{}", pkg.name, pkg.version);
            cache.insert(cache_key, pkg.clone());
        }
    }
}

#[tokio::main]  // async runtime for a program that does no I/O
async fn main() {
    println!("hermeto-templeos v0.1.0 - Blazingly Fast TempleOS Backend");
    println!("God says: {}", god_says());

    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 {
        eprintln!("Usage: hermeto-templeos <lockfile-path> [output-dir]");
        std::process::exit(1);
    }

    let lockfile_path = &args[1];
    let output_dir = args.get(2)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/tmp/templeos_packages"));

    let content = std::fs::read_to_string(lockfile_path)
        .expect("Failed to read lockfile");

    let lockfile = parse_lockfile(&content).expect("Failed to parse lockfile");

    download_packages(&lockfile.packages, &output_dir);

    let components: Vec<SbomComponent> = lockfile.packages.iter()
        .map(|p| p.to_component())
        .collect();

    // Output components as JSON for the Python wrapper to consume
    let json = serde_json::to_string_pretty(&components)
        .expect("Failed to serialize components");
    println!("{}", json);

    println!("God says: {}", god_says());
}
