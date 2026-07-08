use std::thread;
use std::time::Duration;

use clap::Parser;
use indicatif::ProgressBar;
use heck::ToKebabCase;
use rand::random;

use bombadil::sing;
use utils::get_letter_counter;


#[derive(Parser)]
struct Cli {
    word: String,
}

fn main() {
    let args = Cli::parse();
    let wc = get_letter_counter(args.word);
    let pb = ProgressBar::new(wc.count);

    println!("Counting the letters in {:?}", wc.word);

    for _ in 0..wc.count {
        thread::sleep(Duration::from_millis(100));
        pb.inc(1);
    }

    println!("{}", wc);
    
    println!("");
    println!("{}", wc.word.to_kebab_case());
    
    println!("");
    print!("Your lucky number of the day is: {}", random::<u8>());
    
    println!("");
    print!("{}", sing());
}
