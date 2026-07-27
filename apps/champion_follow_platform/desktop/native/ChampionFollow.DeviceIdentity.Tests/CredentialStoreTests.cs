using ChampionFollow.DeviceIdentity;
using Xunit;

namespace ChampionFollow.DeviceIdentity.Tests;

public sealed class CredentialStoreTests
{
    [Fact]
    public void RefreshTokenRoundTripsAndDeletes()
    {
        var target = $"ChampionFollow/Test/{Guid.NewGuid():N}";
        const string value = "secret-fixture";

        try
        {
            CredentialStore.Write(target, value);
            Assert.Equal(value, CredentialStore.Read(target));
        }
        finally
        {
            CredentialStore.Delete(target);
        }

        Assert.Null(CredentialStore.Read(target));
    }
}
