import "server-only";

import qrcode from "qrcode-generator";

/**
 * QR como SVG, gerado no servidor.
 *
 * Por que uma biblioteca e não código nosso: QR é a ISO/IEC 18004, com máscara e correção de
 * erro Reed-Solomon. Implementar isso à mão rende um símbolo que *parece* certo e falha no
 * leitor de alguém — e aqui "falha no leitor" é uma pessoa parada na porta do show.
 *
 * `qrcode-generator` tem zero dependências, o que importa num app com cinco. A alternativa
 * popular (`qrcode`) arrasta um parser de linha de comando que não vamos usar.
 */

//: Correção de erro média (~15%): o ingresso vai ser lido de uma tela com brilho baixo, com
//: dedo em cima e às vezes de um papel amassado. O nível alto engrossaria o símbolo sem
//: necessidade; o baixo quebra no primeiro reflexo.
const CORRECTION = "M" as const;
//: `0` deixa a biblioteca escolher a menor versão que cabe o conteúdo.
const AUTO_VERSION = 0;

export function qrSvg(content: string): string {
  const symbol = qrcode(AUTO_VERSION, CORRECTION);
  symbol.addData(content);
  symbol.make();
  // `margin: 0` porque o espaçamento (a "zona quieta") vem do CSS do cartão, que precisa dela
  // em branco mesmo no tema escuro — margem interna do SVG herdaria o fundo do cartão.
  return symbol.createSvgTag({ cellSize: 6, margin: 0, scalable: true });
}
