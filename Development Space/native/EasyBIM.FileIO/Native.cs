using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace EasyBIM.FileIO
{
    // P/Invoke captures LastError before returning to IronPython's dynamic binder.
    // All paths have already been scoped/validated by the Python package engine.
    // No Autodesk API, registry changes, networking or subprocesses are involved.
    public static class Native
    {
        const uint InvalidAttributes = 0xffffffff;
        static readonly IntPtr InvalidHandle = new IntPtr(-1);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
        static extern uint GetFileAttributesW(string path);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
        static extern bool CreateDirectoryW(string path, IntPtr security);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
        static extern bool DeleteFileW(string path);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
        static extern bool RemoveDirectoryW(string path);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
        static extern bool MoveFileW(string source, string target);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
        static extern SafeFileHandle CreateFileW(string path, uint access, uint share,
            IntPtr security, uint creation, uint flags, IntPtr template);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
        static extern bool GetFileAttributesExW(string path, int level, out AttributeData data);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
        static extern IntPtr FindFirstFileW(string path, out FindData data);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
        static extern bool FindNextFileW(IntPtr handle, out FindData data);
        [DllImport("kernel32.dll", ExactSpelling=true)]
        static extern bool FindClose(IntPtr handle);
        [StructLayout(LayoutKind.Sequential, Pack=4)]
        struct AttributeData
        {
            public uint Attributes;
            public long Created, Accessed, Written;
            public uint High, Low;
        }
        [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode, Pack=4)]
        struct FindData
        {
            public uint Attributes;
            public long Created, Accessed, Written;
            public uint High, Low, Reserved1, Reserved2;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst=260)] public string Name;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst=14)] public string Alternate;
        }
        public sealed class Entry
        {
            public string Name { get; set; }
            public uint Attributes { get; set; }
        }
        public sealed class Stamp
        {
            public ulong Size { get; set; }
            public long Written { get; set; }
        }
        static IOException Failure(int error, string path)
        {
            return new IOException("Windows file operation failed ("+error+"): "+path+
                " — "+new Win32Exception(error).Message);
        }
        public static long Attributes(string path)
        {
            uint value=GetFileAttributesW(path);
            if (value!=InvalidAttributes) return value;
            int error=Marshal.GetLastWin32Error();
            if (error==2 || error==3) return -1;
            throw Failure(error,path);
        }
        public static void MakeDirectory(string path)
        {
            if (CreateDirectoryW(path,IntPtr.Zero)) return;
            int error=Marshal.GetLastWin32Error();
            if (error==183 && (Attributes(path)&16)!=0) return;
            throw Failure(error,path);
        }
        public static void Delete(string path)
        {
            if (DeleteFileW(path)) return;
            int error=Marshal.GetLastWin32Error();
            if (error==2 || error==3) return;
            throw Failure(error,path);
        }
        public static void RemoveDirectory(string path)
        {
            if (!RemoveDirectoryW(path)) throw Failure(Marshal.GetLastWin32Error(),path);
        }
        public static void Move(string source,string target)
        {
            // MoveFileW fails rather than replacing an existing destination.
            if (!MoveFileW(source,target)) throw Failure(Marshal.GetLastWin32Error(),target);
        }
        public static Stamp Signature(string path)
        {
            AttributeData data;
            if (!GetFileAttributesExW(path,0,out data)) throw Failure(Marshal.GetLastWin32Error(),path);
            return new Stamp {Size=((ulong)data.High<<32)|data.Low, Written=data.Written};
        }
        public static FileStream Open(string path,bool writing)
        {
            SafeFileHandle handle=CreateFileW(path,writing?0x40000000u:0x80000000u,
                writing?0u:7u,IntPtr.Zero,writing?1u:3u,128u,IntPtr.Zero);
            if (handle.IsInvalid)
            {
                int error=Marshal.GetLastWin32Error();handle.Dispose();throw Failure(error,path);
            }
            try { return new FileStream(handle,writing?FileAccess.Write:FileAccess.Read,1048576,false); }
            catch { handle.Dispose();throw; }
        }
        public static bool CanReadExclusively(string path)
        {
            using(var handle=CreateFileW(path,0x80000000u,0u,IntPtr.Zero,3u,128u,IntPtr.Zero))
                return !handle.IsInvalid;
        }
        public static Entry[] Entries(string directory)
        {
            FindData data;
            IntPtr handle=FindFirstFileW(directory.TrimEnd('\\')+"\\*",out data);
            if (handle==InvalidHandle)
            {
                int error=Marshal.GetLastWin32Error();
                if (error==2) return new Entry[0];
                throw Failure(error,directory);
            }
            var result=new List<Entry>();
            try
            {
                while (true)
                {
                    if (data.Name!="." && data.Name!="..")
                        result.Add(new Entry{Name=data.Name,Attributes=data.Attributes});
                    if (!FindNextFileW(handle,out data))
                    {
                        int error=Marshal.GetLastWin32Error();
                        if (error!=18) throw Failure(error,directory);
                        break;
                    }
                }
            }
            finally {FindClose(handle);}
            return result.ToArray();
        }
    }
}
