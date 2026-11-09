import { ImageResponse } from "next/og";

import { getStorefrontContext } from "@/lib/server-context";
import { ICON_SIZE, monogram } from "@/lib/store-icon";
import { type Branding, storeTheme } from "@/lib/theme";

// Por loja: o host decide qual (o middleware anexou o contexto).
export const dynamic = "force-dynamic";

/** Ícone da loja (ver lib/store-icon.ts): a sigla sobre a cor da marca. */
export async function GET() {
  const context = await getStorefrontContext();
  if (!context) return new Response(null, { status: 404 });
  const theme = storeTheme(context.branding as Branding);
  const letters = monogram(context.tenant.name);
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: theme.brand,
          color: theme.onBrand,
          fontSize: letters.length > 2 ? 72 : 96,
          letterSpacing: -2,
        }}
      >
        {letters}
      </div>
    ),
    {
      width: ICON_SIZE,
      height: ICON_SIZE,
      // O link leva `?v=` com o que o ícone desenha: endereço novo a cada troca de nome ou cor.
      headers: { "Cache-Control": "public, max-age=604800" },
    },
  );
}
