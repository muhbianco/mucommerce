import Link from "next/link";
import { cookies } from "next/headers";

import { CART_COUNT_COOKIE } from "@/lib/cart-count";

import styles from "./store.module.css";

/**
 * O carrinho que acompanha a pessoa pela loja, no canto de baixo à direita.
 *
 * **Só existe quando há carrinho.** Um botão permanente no canto tapa conteúdo e vira ruído em
 * toda página; aparecendo só com item dentro, ele é uma resposta ao que a pessoa acabou de
 * fazer — e é justamente quando "Adicionar" deixou de levá-la ao carrinho que ele passou a
 * fazer falta.
 *
 * A contagem vem do cookie que as escritas de carrinho já gravam (`cart-actions`), então isto
 * **não custa nenhuma ida ao servidor**. O preço é poder ficar velho entre abas; o cookie vence
 * sozinho em uma hora e `/carrinho` é a verdade. Trocar isso por um `fetch` autenticado em toda
 * página seria caro no lugar mais sensível da loja.
 *
 * O número é decoração: o nome acessível do link diz "Carrinho, 3 itens" por extenso, porque um
 * leitor de tela lendo "3" solto não informa nada.
 */
export async function CartFab() {
  const raw = (await cookies()).get(CART_COUNT_COOKIE)?.value;
  const count = Number(raw);
  if (!Number.isFinite(count) || count <= 0) return null;

  const label = count === 1 ? "Carrinho, 1 item" : `Carrinho, ${count} itens`;
  return (
    <Link href="/carrinho" className={styles.cartFab} aria-label={label}>
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path
          d="M3 4h2l2.6 10.4a1 1 0 0 0 1 .76h8.1a1 1 0 0 0 .98-.8L19.5 7H6"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="9.5" cy="19" r="1.4" fill="currentColor" />
        <circle cx="16.5" cy="19" r="1.4" fill="currentColor" />
      </svg>
      <span className={styles.cartFabCount} aria-hidden="true">
        {count > 99 ? "99+" : count}
      </span>
    </Link>
  );
}
