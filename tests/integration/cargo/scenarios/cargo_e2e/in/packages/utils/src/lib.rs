use std::fmt;

use chrono::{DateTime, Local};

pub struct LetterCounter {
    pub word: String,
    pub count: u64,
    pub timestamp: DateTime<Local>,
}

impl fmt::Display for LetterCounter {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        return write!(f, "The word {} has {} letters.\n{}", self.word, self.count, self.timestamp);
    }
}

pub fn get_letter_counter(word: String) -> LetterCounter {
    let count = word.len().try_into().unwrap();

    LetterCounter {
        word: word,
        count: count,
        timestamp: Local::now(),
    }
}
