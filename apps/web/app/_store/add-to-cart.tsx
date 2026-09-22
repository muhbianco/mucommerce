import { addToCart } from "./cart-actions";
import styles from "./store.module.css";

/** Buy form for a single variant (products without options, ticket lots). */
export function AddToCart({
  variantId,
  back,
  disabled = false,
  label = "Adicionar ao carrinho",
  byWeight = false,
}: {
  variantId: string;
  back: string;
  disabled?: boolean;
  label?: string;
  byWeight?: boolean;
}) {
  return (
    <form action={addToCart} className={styles.buy}>
      <input type="hidden" name="variant_id" value={variantId} />
      <input type="hidden" name="back" value={back} />
      <label>
        {byWeight ? "Quantidade (kg)" : "Quantidade"}{" "}
        <input
          name="quantity"
          inputMode="decimal"
          defaultValue="1"
          size={4}
          pattern={byWeight ? "\\d{1,3}([.,]\\d{1,3})?" : "\\d{1,3}"}
          disabled={disabled}
        />
      </label>{" "}
      <button type="submit" className="button" disabled={disabled}>
        {label}
      </button>
    </form>
  );
}

export const CART_ERRORS: Record<string, string> = {
  out_of_stock: "Não há estoque para essa quantidade.",
  item_unavailable: "Este item não está à venda agora.",
  lot_not_on_sale: "Este lote não está à venda agora.",
  invalid_modifiers: "Confira os adicionais escolhidos.",
  invalid_quantity: "Quantidade inválida.",
  cart_limit: "Seu carrinho já tem itens demais.",
  fulfillment_invalid: "Escolha como receber o pedido.",
  not_found: "Item não encontrado.",
};
