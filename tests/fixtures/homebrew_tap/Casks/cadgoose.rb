cask "cadgoose" do
  version "1.81"
  sha256 "0e00b585a04261082223fff22e705bba824df8c24e035920c3ede77b0abb1084"

  url "https://github.com/ztomer/CadGoose/releases/download/v#{version}/CadGoose-v#{version}.dmg"
  name "CadGoose"
  desc "Agentic overlay companion with multi-goose support and AI chat"
  homepage "https://github.com/ztomer/CadGoose"

  depends_on arch: :arm64
  depends_on macos: :tahoe

  app "CadGoose.app"

  postflight_steps do
    run "/usr/bin/xattr", args: ["-rd", "com.apple.quarantine", "{{appdir}}/CadGoose.app"]
  end

  uninstall quit: "com.desktoppad.CadGoose"

  zap trash: [
    "~/Library/Application Support/CadGoose",
    "~/Library/Logs/CadGoose",
    "~/Library/Preferences/com.desktoppad.CadGoose.plist",
  ]
end
