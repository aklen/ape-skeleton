# Ape skeleton

Workspace vessel: clone this repo, then pull Core, Launcher, and any modules with `./ape sync`.

```bash
git clone https://github.com/aklen/ape-skeleton.git
cd ape-skeleton
cp workspace.yaml.example workspace.yaml
./ape sync
./ape build
```

`./ape build` and `./ape run` need the .NET SDK that matches `TargetFramework` in the checked-out projects. That is **net10.0**. `./ape` reads the csproj files, detects the OS, and prints the install command when the SDK or runtime is missing. `apt install dotnet` is not that package.

`workspace.yaml` lists remotes and stays local to each clone. Copy `workspace.yaml.example` to create it. Checkouts land under `src/` and are gitignored here — their history lives in those repos.

Add a module by appending it under `modules:` and running `./ape sync` again. Each developer keeps their own list. A product stack is this skeleton plus a yaml (and host JSON), not a fork of Core.

## Install .NET 10

Install the **SDK**. It includes the runtime, which is what `./ape build` and `./ape run` need. A runtime-only package can execute a published app; it cannot build this tree.

Projects declare `net10.0`. `./ape` asks for whichever band the csproj files declare.

Check what is installed:

```bash
dotnet --list-sdks
dotnet --list-runtimes
```

### Ubuntu

.NET 10 is in Ubuntu's archive on 24.04 and newer. Ubuntu 22.04 keeps 9.0 and 10.0 in the [.NET backports PPA](https://learn.microsoft.com/en-us/dotnet/core/install/linux-ubuntu-install?tabs=dotnet10&pivots=os-linux-ubuntu-2204).

Ubuntu 22.04:

```bash
sudo add-apt-repository ppa:dotnet/backports
sudo apt-get update && sudo apt-get install -y dotnet-sdk-10.0
```

Ubuntu 24.04, 25.04, 25.10, and 26.04:

```bash
sudo apt-get update && sudo apt-get install -y dotnet-sdk-10.0
```

On 24.04 and 26.04, .NET 9 stays in that backports PPA (`dotnet-sdk-9.0`). On 22.04, .NET 9 uses the same PPA.

To run a published app without the SDK, install `dotnet-runtime-10.0` (or `aspnetcore-runtime-10.0` when the app needs ASP.NET Core). On 22.04 those packages are in the backports PPA too.

### Arch Linux

```bash
sudo pacman -S dotnet-sdk-10.0
```

`dotnet-sdk-10.0` depends on `dotnet-runtime-10.0`. See the [ArchWiki .NET page](https://wiki.archlinux.org/title/.NET).

### macOS

```bash
brew install --cask dotnet-sdk
```

That cask is .NET 10.

Or the pkg installer from [.NET 10 downloads](https://dotnet.microsoft.com/download/dotnet/10.0): **Arm64** on Apple silicon, **x64** on Intel. Steps: [Install .NET on macOS](https://learn.microsoft.com/en-us/dotnet/core/install/macos).

### Windows

```powershell
winget install Microsoft.DotNet.SDK.10
```

Runtime only, without the SDK: `winget install Microsoft.DotNet.Runtime.10`. Steps: [Install .NET on Windows](https://learn.microsoft.com/en-us/dotnet/core/install/windows).

Other Linux distributions: [Install .NET on Linux](https://learn.microsoft.com/en-us/dotnet/core/install/linux).

## Documentation

See [docs/README.md](docs/README.md). After `./ape sync`:

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — workspace map
- [BUILD.md](docs/BUILD.md) — what `./ape build` compiles
- [CONTRIBUTING.md](docs/CONTRIBUTING.md) — style and commits
- Core [DETERMINISM.md](src/Ape.Core/docs/DETERMINISM.md) and [NETWORKING.md](src/Ape.Core/docs/NETWORKING.md)

## License

Copyright (c) 2026 [Akos Hamori](https://github.com/aklen).

Licensed under the [Mozilla Public License 2.0 (MPL-2.0)](https://www.mozilla.org/MPL/2.0/).
