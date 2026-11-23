'use client';

// Small, dependency-free markdown renderer for chat replies.
// Supports headings, lists, tables, blockquotes, fenced code (with copy),
// inline code/bold/italic/strikethrough and http(s) links. Everything becomes
// React nodes — no HTML is ever injected.

import * as React from 'react';
import { Check, Copy } from 'lucide-react';
import { cn } from '@/lib/utils';

export type MarkdownBlock =
  | { kind: 'code'; lang: string; code: string }
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'list'; ordered: boolean; items: string[] }
  | { kind: 'table'; header: string[]; rows: string[][] }
  | { kind: 'quote'; text: string }
  | { kind: 'hr' }
  | { kind: 'paragraph'; text: string };

const HR_RE = /^ {0,3}(-{3,}|\*{3,}|_{3,})\s*$/;
const HEADING_RE = /^ {0,3}(#{1,4})\s+(.*)$/;
const FENCE_RE = /^ {0,3}```([\w+-]*)\s*$/;
const UL_RE = /^\s*[-*+]\s+(.*)$/;
const OL_RE = /^\s*\d+[.)]\s+(.*)$/;
const QUOTE_RE = /^ {0,3}>\s?(.*)$/;
const TABLE_SEP_RE = /^\s*\|?[\s:|-]+\|[\s:|-]*$/;

function splitRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  return trimmed.split('|').map((cell) => cell.trim());
}

export function parseMarkdown(source: string): MarkdownBlock[] {
  const lines = (source ?? '').replace(/\r\n/g, '\n').split('\n');
  const blocks: MarkdownBlock[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) {
      i += 1;
      continue;
    }

    const fence = line.match(FENCE_RE);
    if (fence) {
      const lang = fence[1] || '';
      const code: string[] = [];
      i += 1;
      while (i < lines.length && !FENCE_RE.test(lines[i])) {
        code.push(lines[i]);
        i += 1;
      }
      i += 1; // closing fence (or EOF)
      blocks.push({ kind: 'code', lang, code: code.join('\n') });
      continue;
    }

    if (HR_RE.test(line)) {
      blocks.push({ kind: 'hr' });
      i += 1;
      continue;
    }

    const heading = line.match(HEADING_RE);
    if (heading) {
      blocks.push({ kind: 'heading', level: heading[1].length, text: heading[2].trim() });
      i += 1;
      continue;
    }

    if (QUOTE_RE.test(line)) {
      const quote: string[] = [];
      while (i < lines.length && QUOTE_RE.test(lines[i])) {
        quote.push(lines[i].match(QUOTE_RE)![1]);
        i += 1;
      }
      blocks.push({ kind: 'quote', text: quote.join('\n') });
      continue;
    }

    if (line.includes('|') && i + 1 < lines.length && TABLE_SEP_RE.test(lines[i + 1])) {
      const header = splitRow(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(splitRow(lines[i]));
        i += 1;
      }
      blocks.push({ kind: 'table', header, rows });
      continue;
    }

    if (UL_RE.test(line) || OL_RE.test(line)) {
      const ordered = !UL_RE.test(line);
      const pattern = ordered ? OL_RE : UL_RE;
      const items: string[] = [];
      while (i < lines.length && pattern.test(lines[i])) {
        items.push(lines[i].match(pattern)![1].trim());
        i += 1;
      }
      blocks.push({ kind: 'list', ordered, items });
      continue;
    }

    // Paragraph: consume until a blank line or the start of another block.
    const paragraph: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !FENCE_RE.test(lines[i]) &&
      !HEADING_RE.test(lines[i]) &&
      !HR_RE.test(lines[i]) &&
      !QUOTE_RE.test(lines[i]) &&
      !UL_RE.test(lines[i]) &&
      !OL_RE.test(lines[i])
    ) {
      paragraph.push(lines[i].trim());
      i += 1;
    }
    blocks.push({ kind: 'paragraph', text: paragraph.join('\n') });
  }

  return blocks;
}

const INLINE_RE = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*\n]+\*|_[^_\n]+_|~~[^~]+~~|\[[^\]]+\]\((https?:|mailto:)[^)\s]+\))/g;

function safeHref(href: string): string | null {
  return /^(https?:|mailto:)/i.test(href) ? href : null;
}

export function renderInline(text: string, keyPrefix = 'i'): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  const regex = new RegExp(INLINE_RE);
  let index = 0;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index));
    const token = match[0];
    const key = `${keyPrefix}-${index++}`;

    if (token.startsWith('`')) {
      nodes.push(
        <code key={key} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith('**') || token.startsWith('__')) {
      nodes.push(
        <strong key={key} className="font-semibold">
          {token.slice(2, -2)}
        </strong>,
      );
    } else if (token.startsWith('~~')) {
      nodes.push(
        <s key={key} className="opacity-70">
          {token.slice(2, -2)}
        </s>,
      );
    } else if (token.startsWith('[')) {
      const link = token.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/)!;
      const href = safeHref(link[2]);
      nodes.push(
        href ? (
          <a
            key={key}
            href={href}
            target="_blank"
            rel="noreferrer noopener"
            className="font-medium text-primary underline decoration-primary/40 underline-offset-2 hover:decoration-primary"
          >
            {link[1]}
          </a>
        ) : (
          link[1]
        ),
      );
    } else {
      nodes.push(
        <em key={key} className="italic">
          {token.slice(1, -1)}
        </em>,
      );
    }
    lastIndex = match.index + token.length;
  }

  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = React.useState(false);
  const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  React.useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(text);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard blocked (insecure origin): leave the code selectable.
    }
  };

  return (
    <button
      type="button"
      onClick={copy}
      aria-label={copied ? 'Copied' : 'Copy code'}
      className="oa-icon-button h-6 w-6 text-muted-foreground hover:text-foreground"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

function CodeBlock({ code, lang }: { code: string; lang: string }) {
  return (
    <div className="my-2 overflow-hidden rounded-lg border border-border bg-muted/40">
      <div className="flex items-center justify-between gap-2 border-b border-border/70 px-2.5 py-1">
        <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
          {lang || 'text'}
        </span>
        <CopyButton text={code} />
      </div>
      <pre className="max-h-80 overflow-auto p-2.5 text-[12px] leading-relaxed">
        <code className="font-mono">{code}</code>
      </pre>
    </div>
  );
}

export function Markdown({ content, className }: { content: string; className?: string }) {
  const blocks = React.useMemo(() => parseMarkdown(content), [content]);

  return (
    <div className={cn('text-[13px] leading-relaxed', className)}>
      {blocks.map((block, index) => {
        const key = `b-${index}`;
        switch (block.kind) {
          case 'code':
            return <CodeBlock key={key} code={block.code} lang={block.lang} />;
          case 'heading': {
            const sizes = ['text-base', 'text-[15px]', 'text-sm', 'text-sm'];
            return (
              <p key={key} className={cn('mt-2 mb-1 font-semibold text-foreground first:mt-0', sizes[block.level - 1])}>
                {renderInline(block.text, key)}
              </p>
            );
          }
          case 'hr':
            return <hr key={key} className="my-3 border-border" />;
          case 'quote':
            return (
              <blockquote
                key={key}
                className="my-2 border-l-2 border-primary/40 pl-2.5 text-muted-foreground"
              >
                {renderInline(block.text, key)}
              </blockquote>
            );
          case 'list':
            return block.ordered ? (
              <ol key={key} className="my-1.5 list-decimal space-y-0.5 pl-5">
                {block.items.map((item, itemIndex) => (
                  <li key={`${key}-${itemIndex}`}>{renderInline(item, `${key}-${itemIndex}`)}</li>
                ))}
              </ol>
            ) : (
              <ul key={key} className="my-1.5 list-disc space-y-0.5 pl-5">
                {block.items.map((item, itemIndex) => (
                  <li key={`${key}-${itemIndex}`}>{renderInline(item, `${key}-${itemIndex}`)}</li>
                ))}
              </ul>
            );
          case 'table':
            return (
              <div key={key} className="my-2 overflow-x-auto rounded-lg border border-border">
                <table className="w-full text-left text-[12px]">
                  <thead className="bg-muted/50">
                    <tr>
                      {block.header.map((cell, cellIndex) => (
                        <th key={`${key}-h-${cellIndex}`} className="px-2 py-1.5 font-semibold">
                          {renderInline(cell, `${key}-h-${cellIndex}`)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, rowIndex) => (
                      <tr key={`${key}-r-${rowIndex}`} className="border-t border-border/70">
                        {row.map((cell, cellIndex) => (
                          <td key={`${key}-r-${rowIndex}-${cellIndex}`} className="px-2 py-1.5 align-top">
                            {renderInline(cell, `${key}-r-${rowIndex}-${cellIndex}`)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          default:
            return (
              <p key={key} className="my-1.5 whitespace-pre-wrap first:mt-0 last:mb-0">
                {renderInline(block.text, key)}
              </p>
            );
        }
      })}
    </div>
  );
}
