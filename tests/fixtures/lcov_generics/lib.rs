use std::fmt::Display;

pub fn generic<T: Display>(x: T, flag: bool) -> String {
    if flag {
        return format!("A{x}");
    }
    let s = x.to_string();
    s
}

pub fn never_called() -> String {
    generic::<u8>(3, false)
}

pub fn plain(flag: bool) -> u32 {
    let mut v = 1;
    if flag {
        v += 10;
    }
    v
}

pub fn gen_branch<T: Copy + Into<u64>>(x: T) -> u64 {
    let y: u64 = x.into();
    if y > 1000 {
        return 0;
    }
    y + 1
}

#[inline]
pub fn inl(x: u32) -> u32 {
    if x == 7 {
        return 70;
    }
    x * 2
}

pub fn unused_generic<T: Display>(x: T) -> String {
    format!("{x}")
}

#[cfg(test)]
mod tests {
    #[test]
    fn t() {
        assert_eq!(super::generic(5i32, true), "A5");
        assert_eq!(super::plain(false), 1);
        assert_eq!(super::gen_branch(3u8), 4);
    }
}
