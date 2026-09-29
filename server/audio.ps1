<#
    Cloud HDR — устройства звука: список, выбор по умолчанию, микрофон.

      audio.ps1 list                 все включённые устройства: kind|default|id|имя
      audio.ps1 set <id>             сделать устройство основным (все роли)
      audio.ps1 mic on|off|toggle    микрофон по умолчанию: включить/выключить
      audio.ps1 mic state            muted|unmuted

    Windows не даёт обычным программам выбирать устройство звука — только
    через недокументированный IPolicyConfig (им же пользуются настройки
    Windows и все известные переключатели). Помощник на C# собирается один раз
    в %LOCALAPPDATA%\Cloud HDR\bin и дальше грузится мгновенно.
#>
param([string]$Command = 'list', [string]$Arg = '')
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'

$bin = Join-Path $env:LOCALAPPDATA 'Cloud HDR\bin'
$dll = Join-Path $bin 'CloudAudio.v2.dll'

$source = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace CloudAudio {
    [StructLayout(LayoutKind.Sequential)] public struct PropertyKey { public Guid fmtid; public int pid; }
    [StructLayout(LayoutKind.Explicit, Size = 24)] public struct PropVariant {
        [FieldOffset(0)] public short vt; [FieldOffset(8)] public IntPtr pointer;
    }

    [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceEnumerator {
        [PreserveSig] int EnumAudioEndpoints(int dataFlow, int stateMask, out IMMDeviceCollection devices);
        [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint);
    }
    [ComImport, Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceCollection { [PreserveSig] int GetCount(out int count); [PreserveSig] int Item(int index, out IMMDevice device); }
    [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDevice {
        [PreserveSig] int Activate(ref Guid iid, int clsCtx, IntPtr activationParams, [MarshalAs(UnmanagedType.IUnknown)] out object iface);
        [PreserveSig] int OpenPropertyStore(int access, out IPropertyStore store);
        [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
    }
    [ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyStore {
        [PreserveSig] int GetCount(out int count); [PreserveSig] int GetAt(int index, out PropertyKey key); [PreserveSig] int GetValue(ref PropertyKey key, out PropVariant value);
    }
    [ComImport, Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioEndpointVolume {
        [PreserveSig] int RegisterControlChangeNotify(IntPtr n); [PreserveSig] int UnregisterControlChangeNotify(IntPtr n); [PreserveSig] int GetChannelCount(out int c);
        [PreserveSig] int SetMasterVolumeLevel(float l, ref Guid ctx); [PreserveSig] int SetMasterVolumeLevelScalar(float l, ref Guid ctx);
        [PreserveSig] int GetMasterVolumeLevel(out float l); [PreserveSig] int GetMasterVolumeLevelScalar(out float l);
        [PreserveSig] int SetChannelVolumeLevel(int ch, float l, ref Guid ctx); [PreserveSig] int SetChannelVolumeLevelScalar(int ch, float l, ref Guid ctx);
        [PreserveSig] int GetChannelVolumeLevel(int ch, out float l); [PreserveSig] int GetChannelVolumeLevelScalar(int ch, out float l);
        [PreserveSig] int SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, ref Guid ctx); [PreserveSig] int GetMute([MarshalAs(UnmanagedType.Bool)] out bool mute);
    }
    [ComImport, Guid("f8679f50-850a-41cf-9c72-430f290290c8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPolicyConfig {
        [PreserveSig] int GetMixFormat(); [PreserveSig] int GetDeviceFormat(); [PreserveSig] int ResetDeviceFormat(); [PreserveSig] int SetDeviceFormat();
        [PreserveSig] int GetProcessingPeriod(); [PreserveSig] int SetProcessingPeriod(); [PreserveSig] int GetShareMode(); [PreserveSig] int SetShareMode();
        [PreserveSig] int GetPropertyValue(); [PreserveSig] int SetPropertyValue();
        [PreserveSig] int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string id, int role);
    }
    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumerator { }
    [ComImport, Guid("870af99c-171d-4f9e-af0d-e63df40c2bc9")] class PolicyConfigClient { }

    public static class Audio {
        static PropertyKey FriendlyName = new PropertyKey { fmtid = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), pid = 14 };
        static IMMDeviceEnumerator Enum() { return (IMMDeviceEnumerator)new MMDeviceEnumerator(); }

        static string Name(IMMDevice d) {
            IPropertyStore store; d.OpenPropertyStore(0, out store);
            PropVariant v; store.GetValue(ref FriendlyName, out v);
            return v.vt == 31 ? Marshal.PtrToStringUni(v.pointer) : "";
        }
        static string DefaultId(int flow) {
            IMMDevice d; if (Enum().GetDefaultAudioEndpoint(flow, 0, out d) != 0 || d == null) return "";
            string id; d.GetId(out id); return id;
        }
        // flow: 0 — вывод (колонки, наушники), 1 — ввод (микрофоны)
        public static List<string> List() {
            var rows = new List<string>();
            foreach (int flow in new[] { 0, 1 }) {
                string def = DefaultId(flow);
                IMMDeviceCollection all; Enum().EnumAudioEndpoints(flow, 1, out all);
                int n; all.GetCount(out n);
                for (int i = 0; i < n; i++) {
                    IMMDevice d; all.Item(i, out d); string id; d.GetId(out id);
                    rows.Add((flow == 0 ? "out" : "in") + "|" + (id == def ? "1" : "0") + "|" + id + "|" + Name(d));
                }
            }
            return rows;
        }
        public static void SetDefault(string id) {
            var policy = (IPolicyConfig)new PolicyConfigClient();
            for (int role = 0; role < 3; role++) Marshal.ThrowExceptionForHR(policy.SetDefaultEndpoint(id, role));
        }
        static IAudioEndpointVolume MicVolume() {
            IMMDevice d; Marshal.ThrowExceptionForHR(Enum().GetDefaultAudioEndpoint(1, 0, out d));
            Guid iid = typeof(IAudioEndpointVolume).GUID; object o;
            Marshal.ThrowExceptionForHR(d.Activate(ref iid, 23, IntPtr.Zero, out o));
            return (IAudioEndpointVolume)o;
        }
        public static bool MicMuted() { bool m; MicVolume().GetMute(out m); return m; }
        public static void MicMute(bool mute) { Guid g = Guid.Empty; Marshal.ThrowExceptionForHR(MicVolume().SetMute(mute, ref g)); }
    }
}
'@

if (-not (Test-Path $dll)) {
    New-Item -ItemType Directory -Force $bin | Out-Null
    Add-Type -TypeDefinition $source -OutputAssembly $dll -OutputType Library
}
Add-Type -Path $dll

switch ($Command) {
    'list' { [CloudAudio.Audio]::List() | ForEach-Object { $_ } }
    'set'  { [CloudAudio.Audio]::SetDefault($Arg); 'ok' }
    'mic'  {
        # Микрофона по умолчанию нет вовсе (выдернут из USB) — так и говорим.
        try {
            switch ($Arg) {
                'on'     { [CloudAudio.Audio]::MicMute($false) }
                'off'    { [CloudAudio.Audio]::MicMute($true) }
                'toggle' { [CloudAudio.Audio]::MicMute(-not [CloudAudio.Audio]::MicMuted()) }
            }
            if ([CloudAudio.Audio]::MicMuted()) { 'muted' } else { 'unmuted' }
        } catch { 'none' }
    }
}
