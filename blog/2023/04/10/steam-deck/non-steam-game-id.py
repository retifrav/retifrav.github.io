# based on https://gaming.stackexchange.com/a/386883/131033

import pathlib
import binascii
import pycrc.algorithms
import json


def getSteamShortcutID(executablePath, applicationName, bigPicture=False):
    exeAndName = "".join((executablePath, applicationName))
    if not bigPicture:
        generatedID = binascii.crc32(str.encode(exeAndName)) | 0x80000000
        return generatedID
    else:
        algorithm = pycrc.algorithms.Crc(
            width=32,
            poly=0x04C11DB7,
            reflect_in=True,
            xor_in=0xffffffff,
            reflect_out=True,
            xor_out=0xffffffff
        )
        top32 = algorithm.bit_by_bit(exeAndName) | 0x80000000
        full64 = (top32 << 32) | 0x02000000
        return str(full64)


def getGridArtDestinations(
    steamPath,
    userAccountID,
    executablePath,
    applicationName
):
    grid = pathlib.Path(f"{steamPath}/userdata/{userAccountID}/config/grid")
    shortcut = getSteamShortcutID(executablePath, applicationName)
    bp_shortcut = (shortcut << 32) | 0x02000000
    return {
        "boxart": (grid / f"{shortcut}p.jpg").as_posix(),
        "hero":   (grid / f"{shortcut}_hero.jpg").as_posix(),
        "logo":   (grid / f"{shortcut}_logo.png").as_posix(),
        "10foot": (grid / f"{bp_shortcut}.png").as_posix(),
    }


# print(
#     json.dumps(
#         getGridArtDestinations(
#             "/home/deck/.local/share/Steam",
#             "00000000",
#             "\"/run/media/mmcblk0p1/games/civilization-vi/Base/Binaries/Win64Steam/CivilizationVI_DX12.exe\"",
#             "Civilization VI"
#         ),
#         indent=4
#     )
# )

print("Trying to get 3357182463:")

print(
    "-",
    getSteamShortcutID(
        "\"/run/media/mmcblk0p1/games/civilization-vi/Base/Binaries/Win64Steam/CivilizationVI_DX12.exe\"",
        "Civilization VI"
    )
)
print(
    "-",
    getSteamShortcutID(
        "/run/media/mmcblk0p1/games/civilization-vi/Base/Binaries/Win64Steam/CivilizationVI_DX12.exe",
        "Civilization VI"
    )
)
print(
    "-",
    getSteamShortcutID(
        "\"/home/deck/Downloads/civilization-vi/installer/civ6-setup.exe\"",
        "civ6-setup.exe"
    )
)
print(
    "-",
    getSteamShortcutID(
        "/home/deck/Downloads/civilization-vi/installer/civ6-setup.exe",
        "civ6-setup.exe"
    )
)

print("\nTrying to get 3529560334:")

print(
    "-",
    getSteamShortcutID(
        "\"/home/deck/Downloads/soft/directx_Jun2010_redist.exe\"",
        "directx_Jun2010_redist.exe"
    )
)
print(
    "-",
    getSteamShortcutID(
        "/home/deck/Downloads/soft/directx_Jun2010_redist.exe",
        "directx_Jun2010_redist.exe"
    )
)
