import * as React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { Markdown, parseMarkdown } from './Markdown';

describe('parseMarkdown', () => {
  it('recognises the blocks chat replies actually use', () => {
    const blocks = parseMarkdown(
      [
        '## Summary',
        '',
        'A paragraph with **bold** text.',
        '',
        '- one',
        '- two',
        '',
        '1. first',
        '2. second',
        '',
        '| Name | Value |',
        '| --- | --- |',
        '| a | 1 |',
        '',
        '> quoted',
        '',
        '```ts',
        'const x = 1;',
        '```',
      ].join('\n'),
    );

    expect(blocks.map((block) => block.kind)).toEqual([
      'heading',
      'paragraph',
      'list',
      'list',
      'table',
      'quote',
      'code',
    ]);
    const code = blocks[6];
    expect(code.kind === 'code' && code.lang).toBe('ts');
    expect(code.kind === 'code' && code.code).toBe('const x = 1;');
  });

  it('treats unterminated fences as code to the end', () => {
    const blocks = parseMarkdown('```\nstill code');
    expect(blocks).toEqual([{ kind: 'code', lang: '', code: 'still code' }]);
  });
});

describe('Markdown', () => {
  it('renders formatted text, tables and links without injecting HTML', () => {
    const { container } = render(
      <Markdown
        content={
          'Hello **world** and `code`\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n[docs](https://example.com)\n\n<script>alert(1)</script>'
        }
      />,
    );
    expect(screen.getByText('world').tagName).toBe('STRONG');
    expect(screen.getByText('code').tagName).toBe('CODE');
    expect(screen.getByRole('columnheader', { name: 'A' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: '2' })).toBeInTheDocument();
    const link = screen.getByRole('link', { name: 'docs' });
    expect(link).toHaveAttribute('href', 'https://example.com');
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
    expect(container.querySelector('script')).toBeNull();
    expect(screen.getByText('<script>alert(1)</script>')).toBeInTheDocument();
  });

  it('does not link unsafe protocols', () => {
    render(<Markdown content={'[bad](javascript:alert(1))'} />);
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    // The markdown stays visible as plain text — nothing is clickable.
    expect(screen.getByText('[bad](javascript:alert(1))')).toBeInTheDocument();
  });

  it('copies a fenced code block', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });

    render(<Markdown content={'```bash\npnpm test\n```'} />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy code' }));
    expect(writeText).toHaveBeenCalledWith('pnpm test');
  });
});
