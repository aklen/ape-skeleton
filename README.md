# Ape skeleton

Workspace vessel: clone this repo, then pull Core, Launcher, and any modules with `./ape sync`.

```bash
git clone https://github.com/aklen/ape-skeleton.git
cd ape-skeleton
./ape sync
./ape build
```

`workspace.yaml` lists remotes. Checkouts land under `src/` and are gitignored here — their history lives in those repos.

Add a module by appending it under `modules:` and running `./ape sync` again. A product stack is this skeleton plus a yaml (and host JSON), not a fork of Core.

## Documentation

See [docs/README.md](docs/README.md). After `./ape sync`:

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — workspace map
- [CONTRIBUTING.md](docs/CONTRIBUTING.md) — style and commits
- Core [DETERMINISM.md](src/Ape.Core/docs/DETERMINISM.md) and [NETWORKING.md](src/Ape.Core/docs/NETWORKING.md)

## License

Copyright (c) 2026 [Akos Hamori](https://github.com/aklen).

Licensed under the [Mozilla Public License 2.0 (MPL-2.0)](https://www.mozilla.org/MPL/2.0/).
