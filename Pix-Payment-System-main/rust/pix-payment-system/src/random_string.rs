//! Random string helper — Rust variant (RandomString.java).

use rand::Rng;

const CHARACTERS: &[u8] =
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";

/// Generates a random string of the given length over the alphanumeric alphabet.
pub fn generate_random_string(length: usize) -> String {
    let mut rng = rand::thread_rng();
    (0..length)
        .map(|_| CHARACTERS[rng.gen_range(0..CHARACTERS.len())] as char)
        .collect()
}
