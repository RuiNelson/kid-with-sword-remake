// The settings screen's shared pieces, used by the iOS launcher (Launcher.swift) and the Mac app's settings window (dm/mac/Launcher.swift): one setting as a row (a menu, a slider with
// its percentage, a toggle) and the tabs (SwiftUI's TabView, sidebarAdaptable: a tab bar on the iPhone, a sidebar on the iPad and the Mac), each with its header. A row shows its plain
// description, and its technical one too while Technical details is on (window.technical in the app's preferences, as both launchers keep their own window state).
// A row whose setting is fixed by the launch environment is shown but disabled; one an env file set starts at that value and can still be changed.
import SwiftUI

extension LauncherModel {
    func binding(_ s: Setting) -> Binding<String> { Binding(get: { self.presetValue(s) ?? self.values[s.env] ?? s.def }, set: { self.values[s.env] = $0 }) }
}

struct SettingRow: View {
    let s: Setting
    @ObservedObject var m: LauncherModel
    @AppStorage("window.technical") private var technical = false

    private var preset: String? { m.presetValue(s) }   // the Potato preset sets this row
    private var locked: Bool { m.fixed.contains(s.env) || preset != nil }
    private var lockNote: String? {
        guard locked else { return nil }
        if preset != nil && !m.fixed.contains(s.env) { return "Set by Potato mode" }
        return m.fromLaunch.contains(s.env) ? "Set at launch" : "Set by an env file"
    }
    private var rangeValue: Binding<Double> {
        Binding(get: { Double(preset ?? m.values[s.env] ?? s.def) ?? s.max ?? 1 },
                set: { m.values[s.env] = String(format: "%.2f", $0) })
    }
    private var percentage: String { m.rangeLabel(s, rangeValue.wrappedValue) }

    private var settingLabel: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(s.label)
            if !s.note.isEmpty { Text(s.note).font(.footnote).foregroundStyle(.secondary) }
            if technical, let t = s.tech, !t.isEmpty {
                Label { Text(t) } icon: { Image(systemName: "wrench.and.screwdriver") }
                    .font(.caption).foregroundStyle(.secondary).labelStyle(TechLabelStyle())
            }
            if let l = lockNote { Text(l).font(.footnote.italic()).foregroundStyle(.secondary) }
        }
    }

    var body: some View {
        Group {
            switch s.kind {
            case "choice":
                Picker(selection: m.binding(s)) {
                    ForEach(s.options ?? [], id: \.[0]) { Text($0[1]).tag($0[0]) }
                } label: {
                    settingLabel
                }
                .pickerStyle(.menu)
            case "range":
                VStack(alignment: .leading, spacing: 8) {
                    HStack(alignment: .top) {
                        settingLabel
                        Spacer()
                        Text(percentage).monospacedDigit().foregroundStyle(.secondary)
                    }
                    Slider(value: rangeValue, in: (s.min ?? 0)...(s.max ?? 1), step: s.step ?? 0.05)
                        .accessibilityLabel(s.label)
                        .accessibilityValue(percentage)
                }
            default:
                Toggle(isOn: Binding(get: { (preset ?? m.values[s.env] ?? s.def) == s.onValue }, set: { m.values[s.env] = $0 ? s.onValue : s.offValue })) {
                    settingLabel
                }
            }
        }
        .disabled(locked)
    }
}

/// The technical line: a small icon beside the text, aligned to its first line
private struct TechLabelStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 5) { configuration.icon; configuration.title }
    }
}

/// The settings screen's tabs: SwiftUI's TabView, one tab per section of the list with its icon, the tab last open kept in window.tab. Each tab is a grouped list of its header, its rows,
/// what the launcher adds to it (extra: cloud saves, the memory left), and Reset to tested defaults
struct LauncherTabs<Extra: View>: View {
    @ObservedObject var m: LauncherModel
    @ViewBuilder let extra: (SettingsTab) -> Extra
    @AppStorage("window.tab") private var tab = ""   // the tab last open; the first one on the first start

    var body: some View {
        TabView(selection: Binding(get: { m.tabs.contains { $0.name == tab } ? tab : m.tabs.first?.name ?? "" }, set: { tab = $0 })) {
            ForEach(m.tabs) { t in
                Tab(t.name, systemImage: t.icon, value: t.name) {
                    Form {
                        LauncherTabHeader(tab: t)
                        let rows = m.rows(in: t.name)
                        if !rows.isEmpty {
                            Section("Settings") {
                                ForEach(rows) { SettingRow(s: $0, m: m) }
                            }
                        }
                        extra(t)
                        LauncherResetSection(m: m)
                    }
                    .formStyle(.grouped)
                }
            }
        }
        .tabViewStyle(.sidebarAdaptable)
    }
}

/// The top of a tab: what its settings are for in plain words, the technical description while Technical details is on, and the switch for it
struct LauncherTabHeader: View {
    let tab: SettingsTab
    @AppStorage("window.technical") private var technical = false

    var body: some View {
        Section {
            if !tab.about.isEmpty {
                Label { Text(tab.about) } icon: { Image(systemName: tab.icon).foregroundStyle(.tint) }
            }
            if technical && !tab.tech.isEmpty {
                Label { Text(tab.tech) } icon: { Image(systemName: "wrench.and.screwdriver") }
                    .font(.callout).foregroundStyle(.secondary)
            }
            Toggle(isOn: $technical) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Technical details")
                    Text("Also show how each setting works inside the app.").font(.footnote).foregroundStyle(.secondary)
                }
            }
        }
    }
}

/// The bottom of every tab: Reset to tested defaults
struct LauncherResetSection: View {
    @ObservedObject var m: LauncherModel

    var body: some View {
        Section {
            Button("Reset to tested defaults") { m.reset() }
                .disabled(!m.changed)
        } footer: {
            Text("Brings back all settings this game was tested with, on every tab.")
        }
    }
}
