# Cargo test branch

This branch contains basic Cargo tests. A Cargo project was created with

```shell
$ cargo init --bin
```

Once all dependencies were introduced a Cargo.lock was generated with

```shell
cargo generate-lock
```

The dependencies are intended to be vendored with

```shell
cargo vendor --offline
```
