using System;
using System.IO;

namespace EasyBIM.TempPhase
{
    internal static class AddinInfo
    {
        public const string ProductVersion = "1.1.0";
        public const string ProductName = "EasyBIM Temp Phase";
        public const string TabName = "EasyBIM";
        public const string PanelName = "Misc Tools";
        public const string ButtonText = "Temp Phase";
        public const string ButtonInternalName = "EasyBIM.TempPhase.Button";
        public const string CommandClassName = "EasyBIM.TempPhase.TempPhaseCommand";
        public const string DialogTitle = "Temp Phase";
        public const string VendorId = "RML";
        public const string VendorDescription = "EasyBIM";

        public static string GetAppDataRoot()
        {
            return Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "EasyBIM",
                "Temp Phase");
        }

        public static string GetLogPath()
        {
            return Path.Combine(GetAppDataRoot(), "logs", "events.log");
        }
    }
}
