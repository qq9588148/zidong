using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

namespace ChampionFollow.DeviceIdentity;

public static class CredentialStore
{
    private const uint CredentialTypeGeneric = 1;
    private const uint CredentialPersistLocalMachine = 2;
    private const int ErrorNotFound = 1168;
    private const int MaximumBlobBytes = 5 * 512;

    public static void Write(string target, string value)
    {
        ValidateTarget(target);
        ArgumentNullException.ThrowIfNull(value);

        var blob = Encoding.Unicode.GetBytes(value);
        if (blob.Length > MaximumBlobBytes)
        {
            CryptographicOperations.ZeroMemory(blob);
            throw new ArgumentOutOfRangeException(nameof(value));
        }

        var blobPointer = Marshal.AllocHGlobal(blob.Length);
        try
        {
            if (blob.Length > 0)
            {
                Marshal.Copy(blob, 0, blobPointer, blob.Length);
            }

            var credential = new NativeCredential
            {
                Type = CredentialTypeGeneric,
                TargetName = target,
                CredentialBlobSize = checked((uint)blob.Length),
                CredentialBlob = blobPointer,
                Persist = CredentialPersistLocalMachine,
                UserName = Environment.UserName,
            };

            if (!CredWrite(ref credential, 0))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
        }
        finally
        {
            ZeroUnmanaged(blobPointer, blob.Length);
            Marshal.FreeHGlobal(blobPointer);
            CryptographicOperations.ZeroMemory(blob);
        }
    }

    public static string? Read(string target)
    {
        ValidateTarget(target);
        if (!CredRead(target, CredentialTypeGeneric, 0, out var credentialPointer))
        {
            var error = Marshal.GetLastWin32Error();
            if (error == ErrorNotFound)
            {
                return null;
            }

            throw new Win32Exception(error);
        }

        byte[]? managedBlob = null;
        try
        {
            var credential = Marshal.PtrToStructure<NativeCredential>(credentialPointer);
            managedBlob = new byte[checked((int)credential.CredentialBlobSize)];
            if (managedBlob.Length > 0)
            {
                Marshal.Copy(
                    credential.CredentialBlob,
                    managedBlob,
                    0,
                    managedBlob.Length);
            }

            return Encoding.Unicode.GetString(managedBlob);
        }
        finally
        {
            if (managedBlob is not null)
            {
                var credential = Marshal.PtrToStructure<NativeCredential>(credentialPointer);
                ZeroUnmanaged(credential.CredentialBlob, managedBlob.Length);
                CryptographicOperations.ZeroMemory(managedBlob);
            }

            CredFree(credentialPointer);
        }
    }

    public static void Delete(string target)
    {
        ValidateTarget(target);
        if (CredDelete(target, CredentialTypeGeneric, 0))
        {
            return;
        }

        var error = Marshal.GetLastWin32Error();
        if (error != ErrorNotFound)
        {
            throw new Win32Exception(error);
        }
    }

    private static void ValidateTarget(string target)
    {
        if (string.IsNullOrWhiteSpace(target) || target.Length > 240)
        {
            throw new ArgumentException("Invalid credential target.", nameof(target));
        }
    }

    private static void ZeroUnmanaged(IntPtr pointer, int byteCount)
    {
        for (var index = 0; index < byteCount; index++)
        {
            Marshal.WriteByte(pointer, index, 0);
        }
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct NativeCredential
    {
        public uint Flags;
        public uint Type;

        [MarshalAs(UnmanagedType.LPWStr)]
        public string TargetName;

        [MarshalAs(UnmanagedType.LPWStr)]
        public string? Comment;

        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public uint CredentialBlobSize;
        public IntPtr CredentialBlob;
        public uint Persist;
        public uint AttributeCount;
        public IntPtr Attributes;

        [MarshalAs(UnmanagedType.LPWStr)]
        public string? TargetAlias;

        [MarshalAs(UnmanagedType.LPWStr)]
        public string UserName;
    }

    [DllImport("advapi32.dll", EntryPoint = "CredWriteW", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CredWrite(ref NativeCredential credential, uint flags);

    [DllImport("advapi32.dll", EntryPoint = "CredReadW", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CredRead(
        string target,
        uint type,
        uint reservedFlag,
        out IntPtr credentialPointer);

    [DllImport("advapi32.dll", EntryPoint = "CredDeleteW", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CredDelete(string target, uint type, uint flags);

    [DllImport("advapi32.dll")]
    private static extern void CredFree(IntPtr buffer);
}
