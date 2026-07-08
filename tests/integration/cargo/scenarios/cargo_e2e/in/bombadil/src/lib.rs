extern crate rand;

use rand::prelude::*;

pub fn sing() -> String {
    let n: u8 = random();

    return format!("
Old Tom Bombadil is a merry fellow;
Bright blue his jacket is, and his boots are yellow.
He sang this {} times.\n", n
    );
}
