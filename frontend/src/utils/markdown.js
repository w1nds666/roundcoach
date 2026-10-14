/**
 * Markdown 安全渲染（Phase 5A D 项）
 * - marked 渲染（不执行 raw HTML）+ DOMPurify 消毒（禁 XSS/外链）
 * - 后端已做存储层清洗，前端双保险
 */
import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ breaks: true, gfm: true })

export function renderMarkdown(md) {
  if (!md) return ''
  const rawHtml = marked.parse(md, { async: false })
  return DOMPurify.sanitize(rawHtml, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'form', 'style'],
    FORBID_ATTR: ['onerror', 'onload', 'onclick', 'style'],
  })
}
