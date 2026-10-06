# Page texture crops

64x64 crops of real Arknights Global page textures, used by `test/test_l2d.py` (`PageAlpha`) to check how pages are turned into premultiplied WebP. The art belongs to Hypergryph and Yostar (see `NOTICE.md`).

| File | Source | What it shows |
| --- | --- | --- |
| `masked-rgb.png` | `arts/dynchars/char_1013_chen2_2.ab`, Texture2D `dyn_illust_char_1013_chen2` (ETC_RGB4), x 1696-1759, y 48-111 | The colour of a page shipped with a separate mask. It is already premultiplied by the game. |
| `masked-alpha.png` | The same bundle, Texture2D `dyn_illust_char_1013_chen2[alpha]`, same crop | The separate mask. Its channels are equal, and the blue one becomes the page's alpha. |
| `straight-rgba.png` | `arts/dynchars/char_1044_hsgma2_2.ab`, Texture2D `dyn_illust_char_1044_hsgma2` (ASTC_RGB_5x5), x 240-303, y 48-111 | A page shipped as one RGBA texture: straight alpha with colour under transparent texels. |

Each crop holds texels with alpha 0 and texels with alpha 16-63, the two bands the classifier measures.
