export function buildMarkdownFragment(markdownText, depth = 0) {
    const normalizedText = String(markdownText || "").replace(/\r\n?/g, "\n");
    const lines = normalizedText.split("\n");
    return buildBlocks(lines, 0, depth).fragment;
}

function buildBlocks(lines, startIndex, depth = 0) {
    const fragment = document.createDocumentFragment();
    let index = startIndex;

    while (index < lines.length) {
        if (!lines[index].trim()) {
            index += 1;
            continue;
        }

        const fence = parseFenceStart(lines[index]);
        if (fence) {
            const codeBlock = collectCodeBlock(lines, index, fence);
            fragment.appendChild(
                createCodeBlockElement(codeBlock.content, codeBlock.language),
            );
            index = codeBlock.nextIndex;
            continue;
        }

        const displayMath = parseDisplayMathStart(lines[index]);
        if (displayMath) {
            const mathBlock = collectDisplayMathBlock(lines, index, displayMath);
            fragment.appendChild(createMathElement(mathBlock.content, true));
            index = mathBlock.nextIndex;
            continue;
        }

        if (isHorizontalRuleLine(lines[index])) {
            fragment.appendChild(document.createElement("hr"));
            index += 1;
            continue;
        }

        const heading = parseHeading(lines[index]);
        if (heading) {
            fragment.appendChild(createHeadingElement(heading.level, heading.text));
            index += 1;
            continue;
        }

        if (isBlockquoteLine(lines[index])) {
            const blockquote = collectBlockquote(lines, index);
            fragment.appendChild(createBlockquoteElement(blockquote.lines, depth));
            index = blockquote.nextIndex;
            continue;
        }

        if (isTableStart(lines, index)) {
            const table = collectTable(lines, index);
            fragment.appendChild(table.element);
            index = table.nextIndex;
            continue;
        }

        const listItem = detectListItem(lines[index]);
        if (listItem) {
            const list = collectList(lines, index, listItem.indent);
            fragment.appendChild(list.element);
            index = list.nextIndex;
            continue;
        }

        const paragraph = collectParagraph(lines, index);
        fragment.appendChild(createParagraphElement(paragraph.lines));
        index = paragraph.nextIndex;
    }

    return {
        fragment: fragment,
        nextIndex: index,
    };
}

function parseFenceStart(line) {
    const match = line.match(/^\s*(`{3,}|~{3,})(.*)$/);
    if (!match) {
        return null;
    }

    const language = match[2].trim().split(/\s+/)[0] || "";
    return {
        marker: match[1][0],
        length: match[1].length,
        language: language,
    };
}

function collectCodeBlock(lines, startIndex, fence) {
    const contentLines = [];
    let index = startIndex + 1;

    while (index < lines.length) {
        const closeMatch = lines[index].match(/^\s*(`{3,}|~{3,})\s*$/);
        if (
            closeMatch
            && closeMatch[1][0] === fence.marker
            && closeMatch[1].length >= fence.length
        ) {
            return {
                content: contentLines.join("\n"),
                language: fence.language,
                nextIndex: index + 1,
            };
        }

        contentLines.push(lines[index]);
        index += 1;
    }

    return {
        content: contentLines.join("\n"),
        language: fence.language,
        nextIndex: lines.length,
    };
}

function parseDisplayMathStart(line) {
    const trimmed = line.trim();
    if (trimmed.startsWith("$$")) {
        if (trimmed.endsWith("$$") && trimmed.length > 4) {
            return {
                closingMarker: "$$",
                content: trimmed.slice(2, -2).trim(),
                isSingleLine: true,
            };
        }

        return {
            closingMarker: "$$",
            content: trimmed.slice(2).trim(),
            isSingleLine: false,
        };
    }

    if (trimmed.startsWith("\\[")) {
        if (trimmed.endsWith("\\]") && trimmed.length > 4) {
            return {
                closingMarker: "\\]",
                content: trimmed.slice(2, -2).trim(),
                isSingleLine: true,
            };
        }

        return {
            closingMarker: "\\]",
            content: trimmed.slice(2).trim(),
            isSingleLine: false,
        };
    }

    return null;
}

function collectDisplayMathBlock(lines, startIndex, displayMathStart) {
    if (displayMathStart.isSingleLine) {
        return {
            content: displayMathStart.content,
            nextIndex: startIndex + 1,
        };
    }

    const contentLines = [];
    if (displayMathStart.content) {
        contentLines.push(displayMathStart.content);
    }

    let index = startIndex + 1;
    while (index < lines.length) {
        const trimmed = lines[index].trim();
        if (matchesDisplayMathClose(trimmed, displayMathStart.closingMarker)) {
            const trailingContent = trimmed
                .slice(0, -displayMathStart.closingMarker.length)
                .trim();
            if (trailingContent) {
                contentLines.push(trailingContent);
            }

            return {
                content: contentLines.join("\n").trim(),
                nextIndex: index + 1,
            };
        }

        contentLines.push(lines[index]);
        index += 1;
    }

    return {
        content: contentLines.join("\n").trim(),
        nextIndex: lines.length,
    };
}

function matchesDisplayMathClose(line, marker) {
    return line === marker || line.endsWith(marker);
}

function isHorizontalRuleLine(line) {
    return /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line);
}

function parseHeading(line) {
    const match = line.match(/^\s*(#{1,6})\s+(.*)$/);
    if (!match) {
        return null;
    }

    return {
        level: match[1].length,
        text: match[2].trim(),
    };
}

function isBlockquoteLine(line) {
    return /^\s*>\s?/.test(line);
}

function collectBlockquote(lines, startIndex) {
    const quoteLines = [];
    let index = startIndex;

    while (index < lines.length && isBlockquoteLine(lines[index])) {
        quoteLines.push(lines[index].replace(/^\s*>\s?/, ""));
        index += 1;
    }

    return {
        lines: quoteLines,
        nextIndex: index,
    };
}

function detectListItem(line) {
    const match = line.match(/^([ \t]*)([-*+]|\d+[.)])\s+(.*)$/);
    if (!match) {
        return null;
    }

    const marker = match[2];
    const isOrdered = /^\d/.test(marker);

    return {
        indent: measureIndent(match[1]),
        listType: isOrdered ? "ordered" : "unordered",
        startNumber: isOrdered ? Number.parseInt(marker, 10) : 1,
        content: match[3],
    };
}

function collectList(lines, startIndex, baseIndent) {
    const firstItem = detectListItem(lines[startIndex]);
    const listElement = document.createElement(firstItem.listType === "ordered" ? "ol" : "ul");
    if (firstItem.listType === "ordered" && firstItem.startNumber !== 1) {
        listElement.start = firstItem.startNumber;
    }

    let index = startIndex;
    let previousItem = null;

    // Keep the list parser small and readable: it supports nested lists and
    // task items, but intentionally leaves multi-paragraph list items simple.
    while (index < lines.length) {
        if (!lines[index].trim()) {
            const nextListItem = findNextListItem(lines, index + 1);
            if (!nextListItem || nextListItem.item.indent < baseIndent) {
                break;
            }
            index = nextListItem.index;
            continue;
        }

        const currentItem = detectListItem(lines[index]);
        if (!currentItem) {
            break;
        }

        if (currentItem.indent < baseIndent) {
            break;
        }

        if (currentItem.indent > baseIndent) {
            if (!previousItem) {
                break;
            }

            const nestedList = collectList(lines, index, currentItem.indent);
            previousItem.appendChild(nestedList.element);
            index = nestedList.nextIndex;
            continue;
        }

        if (currentItem.listType !== firstItem.listType) {
            break;
        }

        const listItemElement = createListItemElement(currentItem.content);
        listElement.appendChild(listItemElement);
        previousItem = listItemElement;
        index += 1;
    }

    return {
        element: listElement,
        nextIndex: index,
    };
}

function findNextListItem(lines, startIndex) {
    for (let index = startIndex; index < lines.length; index += 1) {
        if (!lines[index].trim()) {
            continue;
        }

        const listItem = detectListItem(lines[index]);
        if (!listItem) {
            return null;
        }

        return {
            index: index,
            item: listItem,
        };
    }

    return null;
}

function createListItemElement(text) {
    const item = document.createElement("li");
    const content = document.createElement("div");
    content.className = "message-list-item-content";

    const taskState = parseTaskListState(text);
    if (taskState) {
        item.classList.add("message-task-item");

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.className = "message-task-checkbox";
        checkbox.checked = taskState.checked;
        checkbox.disabled = true;

        const taskText = document.createElement("span");
        taskText.className = "message-task-text";
        if (taskState.checked) {
            taskText.classList.add("is-checked");
        }
        appendInlineMarkdown(taskText, taskState.content);

        content.appendChild(checkbox);
        content.appendChild(taskText);
        item.appendChild(content);
        return item;
    }

    appendInlineMarkdown(content, text);
    item.appendChild(content);
    return item;
}

function parseTaskListState(text) {
    const match = String(text || "").match(/^\[( |x|X)\]\s+(.*)$/);
    if (!match) {
        return null;
    }

    return {
        checked: match[1].toLowerCase() === "x",
        content: match[2],
    };
}

function isTableStart(lines, startIndex) {
    if (startIndex + 1 >= lines.length) {
        return false;
    }

    if (!looksLikeTableRow(lines[startIndex])) {
        return false;
    }

    const headerCells = splitTableRow(lines[startIndex]);
    const separatorCells = splitTableRow(lines[startIndex + 1]);
    if (headerCells.length < 2 || headerCells.length !== separatorCells.length) {
        return false;
    }

    return separatorCells.every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s+/g, "")));
}

function collectTable(lines, startIndex) {
    const headerCells = splitTableRow(lines[startIndex]);
    const separatorCells = splitTableRow(lines[startIndex + 1]);
    const alignments = parseTableAlignments(separatorCells);
    const bodyRows = [];

    let index = startIndex + 2;
    while (index < lines.length) {
        if (!lines[index].trim()) {
            break;
        }
        if (!looksLikeTableRow(lines[index])) {
            break;
        }

        bodyRows.push(normalizeTableCells(splitTableRow(lines[index]), headerCells.length));
        index += 1;
    }

    return {
        element: createTableElement(headerCells, alignments, bodyRows),
        nextIndex: index,
    };
}

function looksLikeTableRow(line) {
    return line.includes("|");
}

function splitTableRow(line) {
    let source = String(line || "").trim();
    if (source.startsWith("|")) {
        source = source.slice(1);
    }
    if (source.endsWith("|")) {
        source = source.slice(0, -1);
    }

    const cells = [];
    let currentCell = "";
    let escaped = false;

    for (const character of source) {
        if (escaped) {
            currentCell += character;
            escaped = false;
            continue;
        }

        if (character === "\\") {
            escaped = true;
            continue;
        }

        if (character === "|") {
            cells.push(currentCell.trim());
            currentCell = "";
            continue;
        }

        currentCell += character;
    }

    cells.push(currentCell.trim());
    return cells;
}

function normalizeTableCells(cells, columnCount) {
    const normalizedCells = [...cells];
    while (normalizedCells.length < columnCount) {
        normalizedCells.push("");
    }
    return normalizedCells.slice(0, columnCount);
}

function parseTableAlignments(separatorCells) {
    return separatorCells.map((cell) => {
        const compactCell = cell.replace(/\s+/g, "");
        if (compactCell.startsWith(":") && compactCell.endsWith(":")) {
            return "center";
        }
        if (compactCell.endsWith(":")) {
            return "right";
        }
        return "left";
    });
}

function collectParagraph(lines, startIndex) {
    const paragraphLines = [];
    let index = startIndex;

    while (index < lines.length) {
        if (!lines[index].trim()) {
            break;
        }
        if (paragraphLines.length > 0 && isMarkdownBlockStart(lines, index)) {
            break;
        }

        paragraphLines.push(lines[index].trimEnd());
        index += 1;
    }

    return {
        lines: paragraphLines,
        nextIndex: index,
    };
}

function isMarkdownBlockStart(lines, index) {
    const line = lines[index];
    return Boolean(
        parseFenceStart(line)
        || parseDisplayMathStart(line)
        || isHorizontalRuleLine(line)
        || parseHeading(line)
        || isBlockquoteLine(line)
        || detectListItem(line)
        || isTableStart(lines, index)
    );
}

function createHeadingElement(level, text) {
    const heading = document.createElement(`h${level}`);
    appendInlineMarkdown(heading, text);
    return heading;
}

function createParagraphElement(lines) {
    const paragraph = document.createElement("p");
    appendInlineLines(paragraph, lines);
    return paragraph;
}

function createBlockquoteElement(lines, depth = 0) {
    const blockquote = document.createElement("blockquote");
    let content;
    if (depth >= 20) {
        const p = document.createElement("p");
        p.textContent = lines.join("\n").replace(/^\s*>\s?/gm, "");
        content = document.createDocumentFragment();
        content.appendChild(p);
    } else {
        content = buildMarkdownFragment(lines.join("\n"), depth + 1);
    }
    if (!content.childNodes.length) {
        const paragraph = document.createElement("p");
        blockquote.appendChild(paragraph);
        return blockquote;
    }
    blockquote.appendChild(content);
    return blockquote;
}

function createCodeBlockElement(content, language) {
    const pre = document.createElement("pre");
    pre.className = "message-code-block";
    if (language) {
        pre.dataset.language = language;
    }

    const code = document.createElement("code");
    code.textContent = content;
    pre.appendChild(code);
    return pre;
}

function createMathElement(content, display) {
    const element = document.createElement(display ? "div" : "span");
    element.className = display
        ? "message-math message-math-block"
        : "message-math message-math-inline";
    element.dataset.mathSource = String(content || "");
    element.dataset.mathDisplay = display ? "true" : "false";
    element.dataset.mathRendered = "false";
    element.textContent = String(content || "");
    return element;
}

function createTableElement(headerCells, alignments, bodyRows) {
    const wrapper = document.createElement("div");
    wrapper.className = "message-markdown-table-wrap";

    const table = document.createElement("table");
    table.className = "message-markdown-table";

    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    normalizeTableCells(headerCells, headerCells.length).forEach((cellText, cellIndex) => {
        const cell = document.createElement("th");
        cell.className = alignmentClassFor(alignments[cellIndex]);
        appendInlineMarkdown(cell, cellText);
        headerRow.appendChild(cell);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    if (bodyRows.length > 0) {
        const tbody = document.createElement("tbody");
        bodyRows.forEach((rowCells) => {
            const row = document.createElement("tr");
            rowCells.forEach((cellText, cellIndex) => {
                const cell = document.createElement("td");
                cell.className = alignmentClassFor(alignments[cellIndex]);
                appendInlineMarkdown(cell, cellText);
                row.appendChild(cell);
            });
            tbody.appendChild(row);
        });
        table.appendChild(tbody);
    }

    wrapper.appendChild(table);
    return wrapper;
}

function alignmentClassFor(alignment) {
    if (alignment === "center") {
        return "is-align-center";
    }
    if (alignment === "right") {
        return "is-align-right";
    }
    return "is-align-left";
}

function appendInlineLines(container, lines) {
    lines.forEach((line, lineIndex) => {
        if (lineIndex > 0) {
            container.appendChild(document.createElement("br"));
        }
        appendInlineMarkdown(container, line);
    });
}

function appendInlineMarkdown(container, text, depth = 0) {
    const source = String(text || "");
    if (depth >= 50) {
        container.appendChild(document.createTextNode(source));
        return;
    }
    let index = 0;

    while (index < source.length) {
        const imageToken = parseImageToken(source, index);
        if (imageToken) {
            appendImageToken(container, imageToken);
            index += imageToken.length;
            continue;
        }

        const linkToken = parseLinkToken(source, index)
            || parseAngleAutolinkToken(source, index)
            || parseBareUrlToken(source, index);
        if (linkToken) {
            appendLinkToken(container, linkToken, depth);
            index += linkToken.length;
            continue;
        }

        const codeToken = parseCodeSpanToken(source, index);
        if (codeToken) {
            const code = document.createElement("code");
            code.textContent = codeToken.content;
            container.appendChild(code);
            index += codeToken.length;
            continue;
        }

        const mathToken = parseInlineMathToken(source, index);
        if (mathToken) {
            container.appendChild(createMathElement(mathToken.content, false));
            index += mathToken.length;
            continue;
        }

        const escapeToken = parseEscapeToken(source, index);
        if (escapeToken) {
            container.appendChild(document.createTextNode(escapeToken.content));
            index += escapeToken.length;
            continue;
        }

        const strongToken = parseWrappedToken(source, index, "**")
            || parseWrappedToken(source, index, "__");
        if (strongToken) {
            const strong = document.createElement("strong");
            appendInlineMarkdown(strong, strongToken.content, depth + 1);
            container.appendChild(strong);
            index += strongToken.length;
            continue;
        }

        const strikeToken = parseWrappedToken(source, index, "~~");
        if (strikeToken) {
            const deleted = document.createElement("del");
            appendInlineMarkdown(deleted, strikeToken.content, depth + 1);
            container.appendChild(deleted);
            index += strikeToken.length;
            continue;
        }

        const emphasisToken = parseWrappedToken(source, index, "*")
            || parseWrappedToken(source, index, "_");
        if (emphasisToken) {
            const emphasis = document.createElement("em");
            appendInlineMarkdown(emphasis, emphasisToken.content, depth + 1);
            container.appendChild(emphasis);
            index += emphasisToken.length;
            continue;
        }

        const nextSpecialIndex = findNextInlineSpecialIndex(source, index + 1);
        const sliceEnd = nextSpecialIndex === -1 ? source.length : nextSpecialIndex;
        container.appendChild(document.createTextNode(source.slice(index, sliceEnd)));
        index = sliceEnd;
    }
}

function parseImageToken(text, index) {
    if (!text.startsWith("![", index)) {
        return null;
    }

    const token = parseBracketDestinationToken(text, index + 1);
    if (!token) {
        return null;
    }

    return {
        alt: token.label,
        url: token.url,
        raw: `!${token.raw}`,
        length: token.length + 1,
    };
}

function parseLinkToken(text, index) {
    if (text[index] !== "[") {
        return null;
    }

    return parseBracketDestinationToken(text, index);
}

function parseBracketDestinationToken(text, index) {
    const labelEnd = findClosingBracket(text, index + 1);
    if (labelEnd === -1 || text[labelEnd + 1] !== "(") {
        return null;
    }

    const destinationEnd = findClosingParenthesis(text, labelEnd + 2);
    if (destinationEnd === -1) {
        return null;
    }

    const label = text.slice(index + 1, labelEnd);
    const rawDestination = text.slice(labelEnd + 2, destinationEnd).trim();
    const url = extractLinkDestination(rawDestination);
    if (!label || !url) {
        return null;
    }

    return {
        label: label,
        url: url,
        raw: text.slice(index, destinationEnd + 1),
        length: destinationEnd + 1 - index,
    };
}

function findClosingBracket(text, startIndex) {
    let depth = 0;

    for (let index = startIndex; index < text.length; index += 1) {
        if (text[index - 1] === "\\") {
            continue;
        }

        if (text[index] === "[") {
            depth += 1;
            continue;
        }

        if (text[index] !== "]") {
            continue;
        }

        if (depth === 0) {
            return index;
        }

        depth -= 1;
    }

    return -1;
}

function findClosingParenthesis(text, startIndex) {
    let depth = 0;
    let insideAngleBrackets = false;

    for (let index = startIndex; index < text.length; index += 1) {
        if (text[index - 1] === "\\") {
            continue;
        }

        if (text[index] === "<") {
            insideAngleBrackets = true;
            continue;
        }
        if (text[index] === ">" && insideAngleBrackets) {
            insideAngleBrackets = false;
            continue;
        }
        if (insideAngleBrackets) {
            continue;
        }

        if (text[index] === "(") {
            depth += 1;
            continue;
        }
        if (text[index] !== ")") {
            continue;
        }

        if (depth === 0) {
            return index;
        }

        depth -= 1;
    }

    return -1;
}

function extractLinkDestination(rawDestination) {
    const source = String(rawDestination || "").trim();
    if (!source) {
        return "";
    }

    if (source.startsWith("<") && source.endsWith(">")) {
        return source.slice(1, -1).trim();
    }

    const match = source.match(/^(\S+)/);
    return match ? match[1] : "";
}

function parseAngleAutolinkToken(text, index) {
    if (text[index] !== "<") {
        return null;
    }

    const closingIndex = text.indexOf(">", index + 1);
    if (closingIndex === -1) {
        return null;
    }

    const url = text.slice(index + 1, closingIndex).trim();
    if (!normalizeLinkUrl(url)) {
        return null;
    }

    return {
        label: url,
        url: url,
        raw: text.slice(index, closingIndex + 1),
        length: closingIndex + 1 - index,
    };
}

function parseBareUrlToken(text, index) {
    if (!text.startsWith("http://", index) && !text.startsWith("https://", index)) {
        return null;
    }

    const previousCharacter = text[index - 1] || "";
    if (previousCharacter && /[A-Za-z0-9/]/.test(previousCharacter)) {
        return null;
    }

    const match = text.slice(index).match(/^https?:\/\/[^\s<]+/i);
    if (!match) {
        return null;
    }

    const url = trimTrailingUrlPunctuation(match[0]);
    if (!url) {
        return null;
    }

    return {
        label: url,
        url: url,
        raw: url,
        length: url.length,
    };
}

function trimTrailingUrlPunctuation(url) {
    let trimmed = String(url || "");
    while (trimmed && /[.,!?;:]+$/.test(trimmed)) {
        trimmed = trimmed.slice(0, -1);
    }

    while (
        trimmed.endsWith(")")
        && countCharacter(trimmed, "(") < countCharacter(trimmed, ")")
    ) {
        trimmed = trimmed.slice(0, -1);
    }

    return trimmed;
}

function countCharacter(text, character) {
    let count = 0;
    for (const currentCharacter of String(text || "")) {
        if (currentCharacter === character) {
            count += 1;
        }
    }
    return count;
}

function appendLinkToken(container, token, depth = 0) {
    const safeUrl = normalizeLinkUrl(token.url);
    if (!safeUrl) {
        container.appendChild(document.createTextNode(token.raw));
        return;
    }

    const link = document.createElement("a");
    link.href = safeUrl;
    link.target = "_blank";
    link.rel = "noreferrer noopener";
    appendInlineMarkdown(link, token.label, depth + 1);
    container.appendChild(link);
}

function appendImageToken(container, token) {
    const safeUrl = normalizeImageUrl(token.url);
    if (!safeUrl) {
        container.appendChild(document.createTextNode(token.raw));
        return;
    }

    const previewButton = document.createElement("button");
    previewButton.type = "button";
    previewButton.className = "message-image-link message-markdown-image-link";
    previewButton.dataset.imagePreview = "true";
    previewButton.dataset.imageUrl = safeUrl;
    previewButton.title = token.alt || "Preview image";
    previewButton.setAttribute("aria-label", token.alt || "Preview image");

    const image = document.createElement("img");
    image.className = "message-image message-markdown-image";
    image.src = safeUrl;
    image.alt = token.alt || "Markdown image";
    image.loading = "lazy";

    previewButton.appendChild(image);
    container.appendChild(previewButton);
}

function normalizeLinkUrl(url) {
    const trimmed = String(url || "").trim();
    if (!trimmed) {
        return "";
    }
    if (trimmed.startsWith("/") || trimmed.startsWith("#")) {
        return trimmed;
    }

    try {
        const parsed = new URL(trimmed, window.location.origin);
        if (
            parsed.protocol === "http:"
            || parsed.protocol === "https:"
            || parsed.protocol === "mailto:"
        ) {
            return parsed.href;
        }
    } catch (_error) {
        return "";
    }

    return "";
}

function normalizeImageUrl(url) {
    const trimmed = String(url || "").trim();
    if (!trimmed) {
        return "";
    }

    try {
        const parsed = new URL(trimmed, window.location.origin);
        if (parsed.protocol === "http:" || parsed.protocol === "https:" || parsed.protocol === "blob:") {
            return parsed.href;
        }
        if (parsed.protocol === "data:" && trimmed.startsWith("data:image/")) {
            return trimmed;
        }
    } catch (_error) {
        return "";
    }

    return "";
}

function parseCodeSpanToken(text, index) {
    const markerMatch = text.slice(index).match(/^(`+)/);
    if (!markerMatch) {
        return null;
    }

    const marker = markerMatch[1];
    const closingIndex = text.indexOf(marker, index + marker.length);
    if (closingIndex === -1) {
        return null;
    }

    return {
        content: text.slice(index + marker.length, closingIndex),
        length: closingIndex + marker.length - index,
    };
}

function parseInlineMathToken(text, index) {
    if (text.startsWith("\\(", index)) {
        const closingIndex = findClosingEscapedDelimiter(text, "\\)", index + 2);
        if (closingIndex === -1) {
            return null;
        }

        const content = text.slice(index + 2, closingIndex).trim();
        if (!content) {
            return null;
        }

        return {
            content: content,
            length: closingIndex + 2 - index,
        };
    }

    if (text[index] !== "$" || text[index + 1] === "$") {
        return null;
    }

    const nextCharacter = text[index + 1] || "";
    if (!nextCharacter || /\s/.test(nextCharacter)) {
        return null;
    }

    const closingIndex = findClosingSingleDollar(text, index);
    if (closingIndex === -1) {
        return null;
    }

    const content = text.slice(index + 1, closingIndex);
    if (!content.trim() || /\s/.test(content.at(-1) || "")) {
        return null;
    }

    return {
        content: content,
        length: closingIndex + 1 - index,
    };
}

function findClosingEscapedDelimiter(text, delimiter, startIndex) {
    let searchIndex = startIndex;

    while (searchIndex < text.length) {
        const foundIndex = text.indexOf(delimiter, searchIndex);
        if (foundIndex === -1) {
            return -1;
        }

        if (text[foundIndex - 1] === "\\") {
            searchIndex = foundIndex + delimiter.length;
            continue;
        }

        return foundIndex;
    }

    return -1;
}

function findClosingSingleDollar(text, startIndex) {
    let searchIndex = startIndex + 1;

    while (searchIndex < text.length) {
        const foundIndex = text.indexOf("$", searchIndex);
        if (foundIndex === -1) {
            return -1;
        }

        if (text[foundIndex - 1] === "\\") {
            searchIndex = foundIndex + 1;
            continue;
        }
        if (!text[foundIndex - 1] || /\s/.test(text[foundIndex - 1])) {
            searchIndex = foundIndex + 1;
            continue;
        }

        return foundIndex;
    }

    return -1;
}

function parseEscapeToken(text, index) {
    if (text[index] !== "\\") {
        return null;
    }

    const escapedCharacter = text[index + 1] || "";
    if (!escapedCharacter || !/[[\](){}\\`*_#+.!|~$<>-]/.test(escapedCharacter)) {
        return null;
    }

    return {
        content: escapedCharacter,
        length: 2,
    };
}

function parseWrappedToken(text, index, delimiter) {
    if (!text.startsWith(delimiter, index)) {
        return null;
    }

    const contentStart = index + delimiter.length;
    const closingIndex = findClosingDelimiter(text, delimiter, contentStart);
    if (closingIndex <= contentStart) {
        return null;
    }

    const content = text.slice(contentStart, closingIndex);
    if (!content.trim()) {
        return null;
    }

    return {
        content: content,
        length: closingIndex + delimiter.length - index,
    };
}

function findClosingDelimiter(text, delimiter, startIndex) {
    let searchIndex = startIndex;

    while (searchIndex < text.length) {
        const foundIndex = text.indexOf(delimiter, searchIndex);
        if (foundIndex === -1) {
            return -1;
        }

        if (text[foundIndex - 1] === "\\") {
            searchIndex = foundIndex + delimiter.length;
            continue;
        }

        if (delimiter.length === 1) {
            const previousCharacter = text[foundIndex - 1] || "";
            const nextCharacter = text[foundIndex + 1] || "";
            if (previousCharacter === delimiter || nextCharacter === delimiter) {
                searchIndex = foundIndex + 1;
                continue;
            }
        }

        return foundIndex;
    }

    return -1;
}

function findNextInlineSpecialIndex(text, startIndex) {
    for (let index = startIndex; index < text.length; index += 1) {
        const character = text[index];
        if (
            character === "!"
            || character === "["
            || character === "<"
            || character === "`"
            || character === "*"
            || character === "_"
            || character === "~"
            || character === "$"
            || character === "\\"
            || text.startsWith("http://", index)
            || text.startsWith("https://", index)
        ) {
            return index;
        }
    }
    return -1;
}

function measureIndent(text) {
    return String(text || "").replace(/\t/g, "    ").length;
}
