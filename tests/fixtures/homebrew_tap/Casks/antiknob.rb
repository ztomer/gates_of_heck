cask "antiknob" do
  version "0.15.1"
  sha256 "b207067e3b1f85eb20a812d066cc4222c34a20b582b459306e934075d286b1cc"

  url "https://github.com/ztomer/antiknob/releases/download/v#{version}/Antiknob-v#{version}-aarch64.dmg"
  name "Antiknob"
  desc "Native configurator for the Anticater VK01 knob"
  homepage "https://github.com/ztomer/antiknob"

  # Apple Silicon only — Rust + SwiftUI build targets arm64 (binary minos 26.0).
  depends_on arch: :arm64
  depends_on macos: :tahoe

  app "Antiknob.app"
  app "AntiknobDaemon.app"
  binary "bin/antiknob"
  binary "bin/antiknob-daemon"

  # Strip quarantine so Gatekeeper does not gate the self-signed build.
  # The apps AND the linked CLIs: binary artifacts keep the download's
  # quarantine bit, and a quarantined self-signed CLI hangs on first run
  # while Gatekeeper looks for a notarization ticket that does not exist.
  postflight_steps do
    run "/usr/bin/xattr", args: ["-rd", "com.apple.quarantine", "{{appdir}}/Antiknob.app"]
    run "/usr/bin/xattr", args: ["-rd", "com.apple.quarantine", "{{appdir}}/AntiknobDaemon.app"]
    run "/usr/bin/xattr", args: ["-rd", "com.apple.quarantine", "{{caskroom_path}}/{{version}}/bin/antiknob"]
    run "/usr/bin/xattr", args: ["-rd", "com.apple.quarantine", "{{caskroom_path}}/{{version}}/bin/antiknob-daemon"]
  end

  uninstall quit: [
    "com.antiknob.app",
    "com.antiknob.daemon",
  ]

  zap trash: "~/Library/Application Support/antiknob"
end