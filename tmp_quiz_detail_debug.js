function normalizeOptionLetter(value) {
        const normalized = String(value || '').trim().toUpperCase();
        return /^[A-E]$/.test(normalized) ? normalized : '';
    }

    function getPracticeOptionLetters(question) {
        const options = question && question.options ? Object.keys(question.options) : [];
        const normalized = options
            .map(function(opt) { return normalizeOptionLetter(opt); })
            .filter(function(opt) { return !!opt; })
            .sort();
        return normalized.length > 0 ? normalized : ['A', 'B', 'C', 'D', 'E'];
    }

    function normalizePracticeFuzzyOptions(options, question) {
        const allowed = new Set(getPracticeOptionLetters(question));
        const normalized = [];
        (Array.isArray(options) ? options : []).forEach(function(opt) {
            const letter = normalizeOptionLetter(opt);
            if (letter && allowed.has(letter) && !normalized.includes(letter)) {
                normalized.push(letter);
            }
        });
        return normalized.sort();
    }

    function getPracticeFuzzyOptions(idx) {
        return normalizePracticeFuzzyOptions(practiceFuzzyOptions[idx], practiceQuestions[idx]);
    }

    function setPracticeFuzzyOptions(idx, options) {
        const normalized = normalizePracticeFuzzyOptions(options, practiceQuestions[idx]);
        if (normalized.length > 0) {
            practiceFuzzyOptions[idx] = normalized;
        } else {
            delete practiceFuzzyOptions[idx];
        }
    }

    function clearPracticeFuzzyOptions(idx) {
        delete practiceFuzzyOptions[idx];
    }

    function getConfidenceLabel(confidence) {
        const confMap = {
            sure: 'Q 确定',
            unsure: 'W 模糊',
            no: 'E 不会'
        };
        return confMap[confidence] || '未标注';
    }

    function getPracticeShortcutOptionByKey(key, question) {
        if (!(key >= '1' && key <= '5')) return '';
        const optionLetters = getPracticeOptionLetters(question);
        return optionLetters[parseInt(key, 10) - 1] || '';
    }

    function tryTogglePracticeFuzzyOptionByShortcut(idx, key) {
        if ((practiceConfidence[idx] || '') !== 'unsure') {
            return false;
        }

        const option = getPracticeShortcutOptionByKey(key, practiceQuestions[idx]);
        if (!option) {
            return false;
        }

        togglePracticeFuzzyOption(idx, option);
        return true;
    }

    function buildPracticeFuzzyOptionsPanelHtml(idx) {
        const buttons = getPracticeOptionLetters(practiceQuestions[idx]).map(function(opt) {
            return '<button type="button" onclick="togglePracticeFuzzyOption(' + idx + ', \'' + opt + '\')" ' +
                'id="practice-fuzzy-opt-' + idx + '-' + opt + '" ' +
                'class="practice-fuzzy-chip" aria-pressed="false">' +
                opt + '</button>';
        }).join('');

        return '<div id="practice-fuzzy-panel-' + idx + '" class="practice-fuzzy-panel hidden">' +
            '<div class="practice-fuzzy-title">对哪些选项模糊？可多选</div>' +
            '<div class="practice-fuzzy-choices">' + buttons + '</div>' +
            '<div id="practice-fuzzy-summary-' + idx + '" class="practice-fuzzy-summary">可多选，包括你已选的答案</div>' +
            '</div>';
    }

    function setPracticeElementClassState(element, className, enabled) {
        if (!element || !className) return;

        if (element.classList) {
            if (typeof element.classList.toggle === 'function') {
                element.classList.toggle(className, !!enabled);
                return;
            }
            if (enabled) {
                if (typeof element.classList.add === 'function') element.classList.add(className);
            } else if (typeof element.classList.remove === 'function') {
                element.classList.remove(className);
            }
            return;
        }

        const current = new Set(String(element.className || '').split(/\s+/).filter(Boolean));
        if (enabled) current.add(className);
        else current.delete(className);
        element.className = Array.from(current).join(' ');
    }

    function setPracticePressedState(element, pressed) {
        if (!element) return;

        const value = pressed ? 'true' : 'false';
        if (typeof element.setAttribute === 'function') {
            element.setAttribute('aria-pressed', value);
        } else {
            element.ariaPressed = value;
        }
    }

    function shouldRecordPracticeQuestion(idx) {
        return typeof practiceAnswers !== 'undefined' &&
            !!practiceAnswers &&
            !!practiceAnswers[idx] &&
            typeof detailTrackingSessionId !== 'undefined' &&
            !!detailTrackingSessionId &&
            typeof recordDetailQuestionAnswer === 'function';
    }

    function pulsePracticeFuzzyOptionButton(button) {
        if (!button) return;

        if (button._practiceFuzzyBumpTimer) {
            clearTimeout(button._practiceFuzzyBumpTimer);
        }

        button.classList.remove('is-bump');
        void button.offsetWidth;
        button.classList.add('is-bump');

        button._practiceFuzzyBumpTimer = setTimeout(function() {
            button.classList.remove('is-bump');
            button._practiceFuzzyBumpTimer = null;
        }, 300);
    }

    function updatePracticeFuzzyOptionButtons(idx) {
        const selected = new Set(getPracticeFuzzyOptions(idx));
        const hasSelected = selected.size > 0;
        getPracticeOptionLetters(practiceQuestions[idx]).forEach(function(opt) {
            const button = document.getElementById('practice-fuzzy-opt-' + idx + '-' + opt);
            if (!button) return;

            const isSelected = selected.has(opt);
            const wasSelected = button.classList.contains('is-selected');

            button.classList.add('practice-fuzzy-chip');
            setPracticeElementClassState(button, 'is-selected', isSelected);
            setPracticeElementClassState(button, 'is-muted', hasSelected && !isSelected);
            setPracticeElementClassState(button, 'bg-yellow-500', isSelected);
            setPracticePressedState(button, isSelected);

            if (isSelected && !wasSelected) {
                pulsePracticeFuzzyOptionButton(button);
            } else if (!isSelected && button._practiceFuzzyBumpTimer) {
                clearTimeout(button._practiceFuzzyBumpTimer);
                button._practiceFuzzyBumpTimer = null;
                button.classList.remove('is-bump');
            }
        });
    }

    function pulsePracticeConfidenceButton(button) {
        if (!button) return;

        if (button._practiceConfidenceBumpTimer) {
            clearTimeout(button._practiceConfidenceBumpTimer);
        }

        button.classList.remove('is-bump');
        void button.offsetWidth;
        button.classList.add('is-bump');

        button._practiceConfidenceBumpTimer = setTimeout(function() {
            button.classList.remove('is-bump');
            button._practiceConfidenceBumpTimer = null;
        }, 340);
    }

    function setPracticeConfidenceButtonState(button, isSelected, isMuted) {
        if (!button) return;

        const wasSelected = button.classList.contains('is-selected');

        button.classList.add('practice-confidence-pill');
        setPracticeElementClassState(button, 'is-selected', !!isSelected);
        setPracticeElementClassState(button, 'is-muted', !!isMuted);
        setPracticePressedState(button, isSelected);

        if (isSelected && !wasSelected) {
            pulsePracticeConfidenceButton(button);
        } else if (!isSelected && button._practiceConfidenceBumpTimer) {
            clearTimeout(button._practiceConfidenceBumpTimer);
            button._practiceConfidenceBumpTimer = null;
            button.classList.remove('is-bump');
        }
    }

    function renderPracticeConfidenceState(idx) {
        const level = practiceConfidence[idx] || '';
        const fuzzyOptions = getPracticeFuzzyOptions(idx);
        const btnSure = document.getElementById('practice-btn-sure-' + idx);
        const btnUnsure = document.getElementById('practice-btn-unsure-' + idx);
        const btnNo = document.getElementById('practice-btn-no-' + idx);
        const display = document.getElementById('practice-confidence-display-' + idx);
        const panel = document.getElementById('practice-fuzzy-panel-' + idx);
        const summary = document.getElementById('practice-fuzzy-summary-' + idx);

        const buttonMap = {
            sure: btnSure,
            unsure: btnUnsure,
            no: btnNo
        };

        Object.keys(buttonMap).forEach(function(key) {
            setPracticeConfidenceButtonState(buttonMap[key], level === key, !!level && level !== key);
        });

        if (panel) {
            setPracticeElementClassState(panel, 'hidden', level !== 'unsure');
        }

        if (!display) return;

        display.textContent = '';
        display.className = 'practice-confidence-status hidden';

        if (!level) {
            return;
        }

        if (level === 'sure') {
            display.textContent = '已标记为“确定”，这题按正常节奏完成即可。';
            display.classList.add('is-sure');
        } else if (level === 'unsure') {
            display.textContent = fuzzyOptions.length > 0
                ? '已标记为“模糊”，当前犹豫选项：' + fuzzyOptions.join('、')
                : '已标记为“模糊”，请补充你犹豫的选项。';
            display.classList.add('is-unsure');
            if (summary) {
                summary.textContent = fuzzyOptions.length > 0
                    ? '已标记：' + fuzzyOptions.join('、')
                    : '可多选，包括你已选的答案';
            }
            updatePracticeFuzzyOptionButtons(idx);
        } else {
            display.textContent = '已标记为“不会”，提交后会进入重点复盘。';
            display.classList.add('is-no');
        }

        display.classList.remove('hidden');
    }

    function buildPracticeFuzzyDetailHtml(detail) {
        const fuzzyOptions = Array.isArray(detail && detail.fuzzy_options) ? detail.fuzzy_options : [];
        if (fuzzyOptions.length === 0) {
            return '';
        }

        let html = '<div class="mt-3 rounded-lg bg-yellow-50 border border-yellow-200 px-3 py-2 text-sm text-yellow-800">' +
            '<div class="font-medium">模糊选项：' + fuzzyOptions.join('、') + '</div>';
        fuzzyOptions.forEach(function(opt) {
            const optionText = detail.option_texts && detail.option_texts[opt]
                ? detail.option_texts[opt]
                : ((detail.options && detail.options[opt]) || ('选项' + opt));
            html += '<div class="mt-1">→ ' + opt + '. ' + escapeHtml(optionText) + '</div>';
        });
        html += '</div>';
        return html;
    }

    function buildPracticeFuzzyReportSection(detail) {
        const fuzzyOptions = Array.isArray(detail && detail.fuzzy_options) ? detail.fuzzy_options : [];
        if (fuzzyOptions.length === 0) {
            return '';
        }

        let report = '模糊选项：' + fuzzyOptions.join('、') + '\n';
        fuzzyOptions.forEach(function(opt) {
            const optionText = detail.option_texts && detail.option_texts[opt]
                ? detail.option_texts[opt]
                : ((detail.options && detail.options[opt]) || ('选项' + opt));
            report += '  → ' + opt + '. ' + optionText + '\n';
        });
        return report;
    }

    function normalizePracticeAnswerLetters(answer, question) {
        const allowed = new Set(getPracticeOptionLetters(question));
        const seen = new Set();
        const matches = String(answer || '').toUpperCase().match(/[A-E]/g) || [];

        return matches.filter(function(letter) {
            if (!allowed.has(letter) || seen.has(letter)) {
                return false;
            }
            seen.add(letter);
            return true;
        }).sort();
    }

    function formatPracticeAnswerLetters(letters, emptyText) {
        return letters && letters.length > 0 ? letters.join('、') : (emptyText || '无');
    }

    function analyzePracticeAnswer(detail) {
        const question = detail || {};
        const userLetters = normalizePracticeAnswerLetters(question.user_answer, question);
        const correctLetters = normalizePracticeAnswerLetters(question.correct_answer, question);
        const missingLetters = correctLetters.filter(function(letter) {
            return !userLetters.includes(letter);
        });
        const extraLetters = userLetters.filter(function(letter) {
            return !correctLetters.includes(letter);
        });
        const isMultipleChoice = question.type === 'X';
        let issueLabel = '完全正确';
        let issueTone = 'success';
        let issueNarrative = '你的答案与标准答案一致。';

        if (!question.is_correct) {
            if (userLetters.length === 0) {
                issueLabel = '未作答';
                issueTone = 'neutral';
                issueNarrative = '这题未作答，正确答案是 ' + formatPracticeAnswerLetters(correctLetters, '无') + '。';
            } else if (missingLetters.length > 0 && extraLetters.length > 0) {
                issueLabel = isMultipleChoice ? '少选 + 多选' : '错选';
                issueTone = 'danger';
                issueNarrative = '这题漏选 ' + formatPracticeAnswerLetters(missingLetters) +
                    '，同时' + (isMultipleChoice ? '多选 ' : '错选 ') + formatPracticeAnswerLetters(extraLetters) + '。';
            } else if (missingLetters.length > 0) {
                issueLabel = isMultipleChoice ? '少选' : '错选';
                issueTone = 'warning';
                issueNarrative = isMultipleChoice
                    ? '这题漏选 ' + formatPracticeAnswerLetters(missingLetters) + '。'
                    : '这题没有选到正确答案 ' + formatPracticeAnswerLetters(missingLetters) + '。';
            } else if (extraLetters.length > 0) {
                issueLabel = isMultipleChoice ? '多选' : '错选';
                issueTone = 'danger';
                issueNarrative = '这题' + (isMultipleChoice ? '多选 ' : '错选 ') + formatPracticeAnswerLetters(extraLetters) + '。';
            } else {
                issueLabel = '答案不匹配';
                issueTone = 'danger';
                issueNarrative = '这题答案与标准答案不一致，请结合解析复盘。';
            }
        }

        return {
            isCorrect: !!question.is_correct,
            questionType: question.type || '',
            userLetters: userLetters,
            correctLetters: correctLetters,
            missingLetters: missingLetters,
            extraLetters: extraLetters,
            issueLabel: issueLabel,
            issueTone: issueTone,
            issueNarrative: issueNarrative
        };
    }

    function buildPracticeAnswerSummaryCard(label, value, tone) {
        const toneClassMap = {
            user: 'bg-blue-50 border-blue-200 text-blue-700',
            correct: 'bg-green-50 border-green-200 text-green-700',
            missing: 'bg-yellow-50 border-yellow-200 text-yellow-800',
            extra: 'bg-red-50 border-red-200 text-red-700'
        };
        const toneClass = toneClassMap[tone] || 'bg-gray-50 border-gray-200 text-gray-700';

        return '<div class="rounded-xl border px-4 py-3 ' + toneClass + '">' +
            '<div class="text-xs font-semibold tracking-wide opacity-75">' + escapeHtml(label) + '</div>' +
            '<div class="mt-2 text-base font-bold">' + escapeHtml(value) + '</div>' +
            '</div>';
    }

    function buildPracticeAnswerSummaryHtml(review) {
        const cards = [
            buildPracticeAnswerSummaryCard('你的答案', formatPracticeAnswerLetters(review.userLetters, '未作答'), 'user'),
            buildPracticeAnswerSummaryCard('正确答案', formatPracticeAnswerLetters(review.correctLetters, '无'), 'correct')
        ];

        if (!review.isCorrect && review.missingLetters.length > 0) {
            cards.push(buildPracticeAnswerSummaryCard('漏选项', formatPracticeAnswerLetters(review.missingLetters), 'missing'));
        }

        if (!review.isCorrect && review.extraLetters.length > 0) {
            cards.push(buildPracticeAnswerSummaryCard(
                review.questionType === 'X' ? '多选项' : '错选项',
                formatPracticeAnswerLetters(review.extraLetters),
                'extra'
            ));
        }

        return '<div class="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">' + cards.join('') + '</div>';
    }

    function buildPracticeAnswerIssueHtml(review) {
        if (review.isCorrect) {
            return '';
        }

        const toneClassMap = {
            warning: 'bg-yellow-50 border-yellow-200 text-yellow-800',
            danger: 'bg-red-50 border-red-200 text-red-700',
            neutral: 'bg-gray-50 border-gray-200 text-gray-700'
        };
        const toneClass = toneClassMap[review.issueTone] || toneClassMap.neutral;

        return '<div class="mb-4 rounded-xl border px-4 py-3 ' + toneClass + '">' +
            '<div class="flex flex-wrap items-center gap-2">' +
            '<span class="inline-flex items-center rounded-full bg-white px-2.5 py-1 text-xs font-bold">错因：' +
            escapeHtml(review.issueLabel) + '</span>' +
            '<span class="text-sm font-medium">' + escapeHtml(review.issueNarrative) + '</span>' +
            '</div></div>';
    }

    function buildPracticeAnswerOptionRowHtml(detail, optionLetter, review) {
        const options = detail && detail.options ? detail.options : {};
        const optionText = escapeHtml(options[optionLetter] || ('选项 ' + optionLetter));
        const isSelected = review.userLetters.includes(optionLetter);
        const isCorrect = review.correctLetters.includes(optionLetter);
        let rowClass = 'bg-gray-50 border-gray-200';
        let flagClass = 'bg-white border border-gray-200 text-gray-500';
        let letterClass = 'bg-gray-200 text-gray-600';
        let flagText = '未涉及';

        if (isCorrect && isSelected) {
            rowClass = 'bg-green-50 border-green-200';
            flagClass = 'bg-green-100 text-green-700';
            letterClass = 'bg-green-600 text-white';
            flagText = '选对了';
        } else if (isCorrect) {
            rowClass = 'bg-yellow-50 border-yellow-200';
            flagClass = 'bg-yellow-100 text-yellow-800';
            letterClass = 'bg-yellow-500 text-white';
            flagText = '漏选';
        } else if (isSelected) {
            rowClass = 'bg-red-50 border-red-200';
            flagClass = 'bg-red-100 text-red-700';
            letterClass = 'bg-red-500 text-white';
            flagText = review.questionType === 'X' ? '多选了' : '错选';
        }

        return '<div class="flex flex-col gap-3 rounded-xl border px-4 py-3 sm:flex-row sm:items-start sm:justify-between ' + rowClass + '">' +
            '<div class="flex min-w-0 items-start gap-3">' +
            '<span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-bold ' + letterClass + '">' +
            optionLetter + '</span>' +
            '<span class="min-w-0 text-sm leading-6 text-gray-800">' + optionText + '</span>' +
            '</div>' +
            '<span class="inline-flex shrink-0 items-center rounded-full px-2.5 py-1 text-xs font-bold ' + flagClass + '">' +
            flagText + '</span>' +
            '</div>';
    }

    function buildPracticeAnswerOptionsHtml(detail, review) {
        const rows = getPracticeOptionLetters(detail).map(function(optionLetter) {
            return buildPracticeAnswerOptionRowHtml(detail, optionLetter, review);
        });
        return '<div class="mb-4 space-y-2">' + rows.join('') + '</div>';
    }

    function renderPracticeConfidenceState(idx) {
        const level = practiceConfidence[idx] || '';
        const fuzzyOptions = getPracticeFuzzyOptions(idx);
        const btnSure = document.getElementById('practice-btn-sure-' + idx);
        const btnUnsure = document.getElementById('practice-btn-unsure-' + idx);
        const btnNo = document.getElementById('practice-btn-no-' + idx);
        const display = document.getElementById('practice-confidence-display-' + idx);
        const panel = document.getElementById('practice-fuzzy-panel-' + idx);
        const summary = document.getElementById('practice-fuzzy-summary-' + idx);

        const buttonMap = {
            sure: btnSure,
            unsure: btnUnsure,
            no: btnNo
        };

        Object.keys(buttonMap).forEach(function(key) {
            setPracticeConfidenceButtonState(buttonMap[key], level === key, !!level && level !== key);
        });

        if (panel) {
            setPracticeElementClassState(panel, 'hidden', level !== 'unsure');
        }

        if (!display) return;

        display.textContent = '';
        display.className = 'practice-confidence-status hidden';

        if (!level) {
            return;
        }

        if (level === 'sure') {
            display.textContent = '已标记为“确定”，这题按正常节奏完成即可。';
            display.classList.add('is-sure');
        } else if (level === 'unsure') {
            display.textContent = fuzzyOptions.length > 0
                ? '？模糊选项：' + fuzzyOptions.join('、')
                : '已标记为“模糊”，请勾选具体选项。';
            display.classList.add('is-unsure');
            if (summary) {
                summary.textContent = fuzzyOptions.length > 0
                    ? '已标记：' + fuzzyOptions.join('、')
                    : '请勾选具体选项，可多选，包括你已选的答案';
            }
            updatePracticeFuzzyOptionButtons(idx);
        } else {
            display.textContent = '已标记为“不会”，提交后会进入重点复盘。';
            display.classList.add('is-no');
        }

        display.classList.remove('hidden');
    }

    
function setPracticeConfidenceButtonState(button, isSelected, isMuted) {
        if (!button) return;

        const wasSelected = button.classList.contains('is-selected');

        button.classList.add('practice-confidence-pill');
        setPracticeElementClassState(button, 'is-selected', !!isSelected);
        setPracticeElementClassState(button, 'is-muted', !!isMuted);
        setPracticePressedState(button, isSelected);

        if (isSelected && !wasSelected) {
            pulsePracticeConfidenceButton(button);
        } else if (!isSelected && button._practiceConfidenceBumpTimer) {
            clearTimeout(button._practiceConfidenceBumpTimer);
            button._practiceConfidenceBumpTimer = null;
            button.classList.remove('is-bump');
        }
    }

    function renderPracticeConfidenceState(idx) {
        const level = practiceConfidence[idx] || '';
        const fuzzyOptions = getPracticeFuzzyOptions(idx);
        const btnSure = document.getElementById('practice-btn-sure-' + idx);
        const btnUnsure = document.getElementById('practice-btn-unsure-' + idx);
        const btnNo = document.getElementById('practice-btn-no-' + idx);
        const display = document.getElementById('practice-confidence-display-' + idx);
        const panel = document.getElementById('practice-fuzzy-panel-' + idx);
        const summary = document.getElementById('practice-fuzzy-summary-' + idx);

        const buttonMap = {
            sure: btnSure,
            unsure: btnUnsure,
            no: btnNo
        };

        Object.keys(buttonMap).forEach(function(key) {
            setPracticeConfidenceButtonState(buttonMap[key], level === key, !!level && level !== key);
        });

        if (panel) {
            setPracticeElementClassState(panel, 'hidden', level !== 'unsure');
        }

        if (!display) return;

        display.textContent = '';
        display.className = 'practice-confidence-status hidden';

        if (!level) {
            return;
        }

        if (level === 'sure') {
            display.textContent = '已标记为“确定”，这题按正常节奏完成即可。';
            display.classList.add('is-sure');
        } else if (level === 'unsure') {
            display.textContent = fuzzyOptions.length > 0
                ? '已标记为“模糊”，当前犹豫选项：' + fuzzyOptions.join('、')
                : '已标记为“模糊”，请补充你犹豫的选项。';
            display.classList.add('is-unsure');
            if (summary) {
                summary.textContent = fuzzyOptions.length > 0
                    ? '已标记：' + fuzzyOptions.join('、')
                    : '可多选，包括你已选的答案';
            }
            updatePracticeFuzzyOptionButtons(idx);
        } else {
            display.textContent = '已标记为“不会”，提交后会进入重点复盘。';
            display.classList.add('is-no');
        }

        display.classList.remove('hidden');
    }

    function buildPracticeFuzzyDetailHtml(detail) {
        const fuzzyOptions = Array.isArray(detail && detail.fuzzy_options) ? detail.fuzzy_options : [];
        if (fuzzyOptions.length === 0) {
            return '';
        }

        let html = '<div class="mt-3 rounded-lg bg-yellow-50 border border-yellow-200 px-3 py-2 text-sm text-yellow-800">' +
            '<div class="font-medium">模糊选项：' + fuzzyOptions.join('、') + '</div>';
        fuzzyOptions.forEach(function(opt) {
            const optionText = detail.option_texts && detail.option_texts[opt]
                ? detail.option_texts[opt]
                : ((detail.options && detail.options[opt]) || ('选项' + opt));
            html += '<div class="mt-1">→ ' + opt + '. ' + escapeHtml(optionText) + '</div>';
        });
        html += '</div>';
        return html;
    }

    function buildPracticeFuzzyReportSection(detail) {
        const fuzzyOptions = Array.isArray(detail && detail.fuzzy_options) ? detail.fuzzy_options : [];
        if (fuzzyOptions.length === 0) {
            return '';
        }

        let report = '模糊选项：' + fuzzyOptions.join('、') + '\n';
        fuzzyOptions.forEach(function(opt) {
            const optionText = detail.option_texts && detail.option_texts[opt]
                ? detail.option_texts[opt]
                : ((detail.options && detail.options[opt]) || ('选项' + opt));
            report += '  → ' + opt + '. ' + optionText + '\n';
        });
        return report;
    }

    function normalizePracticeAnswerLetters(answer, question) {
        const allowed = new Set(getPracticeOptionLetters(question));
        const seen = new Set();
        const matches = String(answer || '').toUpperCase().match(/[A-E]/g) || [];

        return matches.filter(function(letter) {
            if (!allowed.has(letter) || seen.has(letter)) {
                return false;
            }
            seen.add(letter);
            return true;
        }).sort();
    }

    function formatPracticeAnswerLetters(letters, emptyText) {
        return letters && letters.length > 0 ? letters.join('、') : (emptyText || '无');
    }

    function analyzePracticeAnswer(detail) {
        const question = detail || {};
        const userLetters = normalizePracticeAnswerLetters(question.user_answer, question);
        const correctLetters = normalizePracticeAnswerLetters(question.correct_answer, question);
        const missingLetters = correctLetters.filter(function(letter) {
            return !userLetters.includes(letter);
        });
        const extraLetters = userLetters.filter(function(letter) {
            return !correctLetters.includes(letter);
        });
        const isMultipleChoice = question.type === 'X';
        let issueLabel = '完全正确';
        let issueTone = 'success';
        let issueNarrative = '你的答案与标准答案一致。';

        if (!question.is_correct) {
            if (userLetters.length === 0) {
                issueLabel = '未作答';
                issueTone = 'neutral';
                issueNarrative = '这题未作答，正确答案是 ' + formatPracticeAnswerLetters(correctLetters, '无') + '。';
            } else if (missingLetters.length > 0 && extraLetters.length > 0) {
                issueLabel = isMultipleChoice ? '少选 + 多选' : '错选';
                issueTone = 'danger';
                issueNarrative = '这题漏选 ' + formatPracticeAnswerLetters(missingLetters) +
                    '，同时' + (isMultipleChoice ? '多选 ' : '错选 ') + formatPracticeAnswerLetters(extraLetters) + '。';
            } else if (missingLetters.length > 0) {
                issueLabel = isMultipleChoice ? '少选' : '错选';
                issueTone = 'warning';
                issueNarrative = isMultipleChoice
                    ? '这题漏选 ' + formatPracticeAnswerLetters(missingLetters) + '。'
                    : '这题没有选到正确答案 ' + formatPracticeAnswerLetters(missingLetters) + '。';
            } else if (extraLetters.length > 0) {
                issueLabel = isMultipleChoice ? '多选' : '错选';
                issueTone = 'danger';
                issueNarrative = '这题' + (isMultipleChoice ? '多选 ' : '错选 ') + formatPracticeAnswerLetters(extraLetters) + '。';
            } else {
                issueLabel = '答案不匹配';
                issueTone = 'danger';
                issueNarrative = '这题答案与标准答案不一致，请结合解析复盘。';
            }
        }

        return {
            isCorrect: !!question.is_correct,
            questionType: question.type || '',
            userLetters: userLetters,
            correctLetters: correctLetters,
            missingLetters: missingLetters,
            extraLetters: extraLetters,
            issueLabel: issueLabel,
            issueTone: issueTone,
            issueNarrative: issueNarrative
        };
    }

    function buildPracticeAnswerSummaryCard(label, value, tone) {
        const toneClassMap = {
            user: 'bg-blue-50 border-blue-200 text-blue-700',
            correct: 'bg-green-50 border-green-200 text-green-700',
            missing: 'bg-yellow-50 border-yellow-200 text-yellow-800',
            extra: 'bg-red-50 border-red-200 text-red-700'
        };
        const toneClass = toneClassMap[tone] || 'bg-gray-50 border-gray-200 text-gray-700';

        return '<div class="rounded-xl border px-4 py-3 ' + toneClass + '">' +
            '<div class="text-xs font-semibold tracking-wide opacity-75">' + escapeHtml(label) + '</div>' +
            '<div class="mt-2 text-base font-bold">' + escapeHtml(value) + '</div>' +
            '</div>';
    }

    function buildPracticeAnswerSummaryHtml(review) {
        const cards = [
            buildPracticeAnswerSummaryCard('你的答案', formatPracticeAnswerLetters(review.userLetters, '未作答'), 'user'),
            buildPracticeAnswerSummaryCard('正确答案', formatPracticeAnswerLetters(review.correctLetters, '无'), 'correct')
        ];

        if (!review.isCorrect && review.missingLetters.length > 0) {
            cards.push(buildPracticeAnswerSummaryCard('漏选项', formatPracticeAnswerLetters(review.missingLetters), 'missing'));
        }

        if (!review.isCorrect && review.extraLetters.length > 0) {
            cards.push(buildPracticeAnswerSummaryCard(
                review.questionType === 'X' ? '多选项' : '错选项',
                formatPracticeAnswerLetters(review.extraLetters),
                'extra'
            ));
        }

        return '<div class="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">' + cards.join('') + '</div>';
    }

    function buildPracticeAnswerIssueHtml(review) {
        if (review.isCorrect) {
            return '';
        }

        const toneClassMap = {
            warning: 'bg-yellow-50 border-yellow-200 text-yellow-800',
            danger: 'bg-red-50 border-red-200 text-red-700',
            neutral: 'bg-gray-50 border-gray-200 text-gray-700'
        };
        const toneClass = toneClassMap[review.issueTone] || toneClassMap.neutral;

        return '<div class="mb-4 rounded-xl border px-4 py-3 ' + toneClass + '">' +
            '<div class="flex flex-wrap items-center gap-2">' +
            '<span class="inline-flex items-center rounded-full bg-white px-2.5 py-1 text-xs font-bold">错因：' +
            escapeHtml(review.issueLabel) + '</span>' +
            '<span class="text-sm font-medium">' + escapeHtml(review.issueNarrative) + '</span>' +
            '</div></div>';
    }

    function buildPracticeAnswerOptionRowHtml(detail, optionLetter, review) {
        const options = detail && detail.options ? detail.options : {};
        const optionText = escapeHtml(options[optionLetter] || ('选项 ' + optionLetter));
        const isSelected = review.userLetters.includes(optionLetter);
        const isCorrect = review.correctLetters.includes(optionLetter);
        let rowClass = 'bg-gray-50 border-gray-200';
        let flagClass = 'bg-white border border-gray-200 text-gray-500';
        let letterClass = 'bg-gray-200 text-gray-600';
        let flagText = '未涉及';

        if (isCorrect && isSelected) {
            rowClass = 'bg-green-50 border-green-200';
            flagClass = 'bg-green-100 text-green-700';
            letterClass = 'bg-green-600 text-white';
            flagText = '选对了';
        } else if (isCorrect) {
            rowClass = 'bg-yellow-50 border-yellow-200';
            flagClass = 'bg-yellow-100 text-yellow-800';
            letterClass = 'bg-yellow-500 text-white';
            flagText = '漏选';
        } else if (isSelected) {
            rowClass = 'bg-red-50 border-red-200';
            flagClass = 'bg-red-100 text-red-700';
            letterClass = 'bg-red-500 text-white';
            flagText = review.questionType === 'X' ? '多选了' : '错选';
        }

        return '<div class="flex flex-col gap-3 rounded-xl border px-4 py-3 sm:flex-row sm:items-start sm:justify-between ' + rowClass + '">' +
            '<div class="flex min-w-0 items-start gap-3">' +
            '<span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-bold ' + letterClass + '">' +
            optionLetter + '</span>' +
            '<span class="min-w-0 text-sm leading-6 text-gray-800">' + optionText + '</span>' +
            '</div>' +
            '<span class="inline-flex shrink-0 items-center rounded-full px-2.5 py-1 text-xs font-bold ' + flagClass + '">' +
            flagText + '</span>' +
            '</div>';
    }

    function buildPracticeAnswerOptionsHtml(detail, review) {
        const rows = getPracticeOptionLetters(detail).map(function(optionLetter) {
            return buildPracticeAnswerOptionRowHtml(detail, optionLetter, review);
        });
        return '<div class="mb-4 space-y-2">' + rows.join('') + '</div>';
    }

    function renderPracticeConfidenceState(idx) {
        const level = practiceConfidence[idx] || '';
        const fuzzyOptions = getPracticeFuzzyOptions(idx);
        const btnSure = document.getElementById('practice-btn-sure-' + idx);
        const btnUnsure = document.getElementById('practice-btn-unsure-' + idx);
        const btnNo = document.getElementById('practice-btn-no-' + idx);
        const display = document.getElementById('practice-confidence-display-' + idx);
        const panel = document.getElementById('practice-fuzzy-panel-' + idx);
        const summary = document.getElementById('practice-fuzzy-summary-' + idx);

        const buttonMap = {
            sure: btnSure,
            unsure: btnUnsure,
            no: btnNo
        };

        Object.keys(buttonMap).forEach(function(key) {
            setPracticeConfidenceButtonState(buttonMap[key], level === key, !!level && level !== key);
        });

        if (panel) {
            setPracticeElementClassState(panel, 'hidden', level !== 'unsure');
        }

        if (!display) return;

        display.textContent = '';
        display.className = 'practice-confidence-status hidden';

        if (!level) {
            return;
        }

        if (level === 'sure') {
            display.textContent = '已标记为“确定”，这题按正常节奏完成即可。';
            display.classList.add('is-sure');
        } else if (level === 'unsure') {
            display.textContent = fuzzyOptions.length > 0
                ? '？模糊选项：' + fuzzyOptions.join('、')
                : '已标记为“模糊”，请勾选具体选项。';
            display.classList.add('is-unsure');
            if (summary) {
                summary.textContent = fuzzyOptions.length > 0
                    ? '已标记：' + fuzzyOptions.join('、')
                    : '请勾选具体选项，可多选，包括你已选的答案';
            }
            updatePracticeFuzzyOptionButtons(idx);
        } else {
            display.textContent = '已标记为“不会”，提交后会进入重点复盘。';
            display.classList.add('is-no');
        }

        display.classList.remove('hidden');
    }

    function getSeverityBadgeText(tag) {
        if (tag === 'stubborn') return '顽固病灶';
        if (tag === 'critical') return '致命盲区';
        if (tag === 'landmine') return '隐形地雷';
        if (tag === 'normal') return '普通错误';
        return '';
    }

    function buildMaskedExplanationHtml(explanation, isCorrect, confidence, revealId) {
        const text = explanation || '暂无解析';
        if (!explanation || (isCorrect && confidence === 'sure')) {
            return '<div class="bg-gray-50 p-4 rounded-lg"><div class="font-medium text-gray-700 mb-2">解析</div><p class="text-gray-600 text-sm">' + text + '</p></div>';
        }
        return '<div class="bg-gray-50 p-4 rounded-lg"><div class="font-medium text-gray-700 mb-2">解析</div>' +
            '<button type="button" onclick="revealMaskedExplanation(\'' + revealId + '\', this)" class="w-full text-left bg-amber-50 border border-amber-300 text-amber-700 px-3 py-2 rounded-lg text-sm hover:bg-amber-100 transition">先自行反思后，再查看解析</button>' +
            '<p id="' + revealId + '" class="text-gray-600 text-sm mt-3 hidden">' + text + '</p></div>';
    }

    async function loadExamData() {
        if (!examId) {
            console.log('没有 examId，无法加载数据');
            return null;
        }

        try {
            return await safeFetch('/api/quiz/batch/detail/' + examId, {}, '操作失败');
        } catch (e) {
            console.error('加载试卷数据失败:', e);
            return null;
        }
    }

    async function startDetailTrackingSession(knowledgePoint) {
        try {
            const data = await safeFetch('/api/tracking/session/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    session_type: 'detail_practice',
                    chapter_id: examData.chapter_id,
                    title: `细节练习: ${knowledgePoint}`,
                    knowledge_point: knowledgePoint
                })
            }, '操作失败');
            detailTrackingSessionId = data.session_id;
            detailTrackingCompleted = false;
            console.log('[Tracking] 细节练习会话已开启:', detailTrackingSessionId);
        } catch (error) {
            console.error('[Tracking] 开始细节练习会话失败:', error);
        }
    }

    async function recordDetailQuestionAnswer(questionIndex) {
        if (!detailTrackingSessionId) return;

        const q = practiceQuestions[questionIndex];
        if (!q) return;

        const answer = practiceAnswers[questionIndex] || '';
        const confidence = practiceConfidence[questionIndex] || '';

        // 清理答案格式函数
        const cleanAnswer = function(ans) {
            return (ans || '').toString().toUpperCase().replace(/[^A-E]/g, '');
        };

        const cleanUserAnswer = cleanAnswer(answer);
        const cleanCorrectAnswer = cleanAnswer(q.correct_answer);

        try {
            await safeFetch('/api/tracking/session/' + detailTrackingSessionId + '/question', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    question_index: questionIndex,
                    question_type: q.type,
                    difficulty: q.difficulty,
                    question_text: q.question,
                    options: q.options,
                    correct_answer: q.correct_answer,
                    user_answer: answer,
                    is_correct: (q.type === 'X')
                        ? cleanUserAnswer.split('').sort().join('') === cleanCorrectAnswer.split('').sort().join('')
                        : cleanUserAnswer === cleanCorrectAnswer,
                    confidence: confidence,
                    explanation: q.explanation,
                    key_point: currentKnowledge
                })
            }, '操作失败');
        } catch (error) {
            console.error('[Tracking] 记录细节练习题目失败:', error);
        }
    }

    async function completeDetailTrackingSession(score, totalQuestions) {
        if (!detailTrackingSessionId) return;
        try {
            await safeFetch('/api/tracking/session/' + detailTrackingSessionId + '/complete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    score: score,
                    total_questions: totalQuestions
                })
            }, '操作失败');
            console.log('[Tracking] 细节练习会话完成');
        } catch (error) {
            console.error('[Tracking] 完成细节练习会话失败:', error);
        }
    }

    const originalCompleteDetailTrackingSession = completeDetailTrackingSession;
    completeDetailTrackingSession = async function(score, totalQuestions) {
        await originalCompleteDetailTrackingSession(score, totalQuestions);
        if (!detailTrackingCompleted && currentKnowledge && detailTrackingSessionId) {
            detailTrackingCompleted = true;
            bumpKnowledgePracticeCount(currentKnowledge);
        }
    };

    async function generateVariationQuestions(keyPoint) {
        const k = knowledgeMap[keyPoint];
        if (!k) {
            alert('知识点数据不存在');
            return;
        }

        const container = document.getElementById('questionsContainer');
        container.innerHTML =
            '<div class="text-center p-8">' +
            '<div class="tls-loader-wrapper tls-loader-wrapper--compact mb-5" aria-hidden="true">' +
            '<span class="tls-loader-letter" style="--loader-letter-index: 0;">G</span>' +
            '<span class="tls-loader-letter" style="--loader-letter-index: 1;">e</span>' +
            '<span class="tls-loader-letter" style="--loader-letter-index: 2;">n</span>' +
            '<span class="tls-loader-letter" style="--loader-letter-index: 3;">e</span>' +
            '<span class="tls-loader-letter" style="--loader-letter-index: 4;">r</span>' +
            '<span class="tls-loader-letter" style="--loader-letter-index: 5;">a</span>' +
            '<span class="tls-loader-letter" style="--loader-letter-index: 6;">t</span>' +
            '<span class="tls-loader-letter" style="--loader-letter-index: 7;">i</span>' +
            '<span class="tls-loader-letter" style="--loader-letter-index: 8;">n</span>' +
            '<span class="tls-loader-letter" style="--loader-letter-index: 9;">g</span>' +
            '<div class="tls-loader"></div>' +
            '</div>' +
            '<div class="text-lg">DeepSeek 正在生成变式题...</div>' +
            '<div class="text-sm text-gray-400 mt-2">基于同一知识点，生成 5 道不同角度的变式</div>' +
            '</div>';
        document.getElementById('questionArea').classList.remove('hidden');

        let generationFailed = false;
        try {
            const resp = await fetch('/api/quiz/batch/generate-variations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    key_point: keyPoint,
                    base_question: k,
                    uploaded_content: examData.uploadedContent || '',
                    num_variations: 5
                })
            });
            const data = await resp.json();

            // 检查后端是否返回了 fallback 标记
            if (data.is_fallback || data.error) {
                console.error('[细节练习] 后端返回生成失败:', data.error);
                generationFailed = true;
            } else {
                practiceQuestions = (data.variations || []).filter(v => v && v.question);
                console.log('[细节练习] AI生成变式题数量:', practiceQuestions.length);
                if (practiceQuestions.length === 0) {
                    generationFailed = true;
                }
            }
        } catch (error) {
            console.error('[细节练习] 请求失败:', error);
            generationFailed = true;
        }

        // 生成失败：显示重试界面
        if (generationFailed) {
            container.innerHTML =
                '<div class="bg-red-50 border-l-4 border-red-400 p-6 rounded-lg">' +
                '<div class="text-lg font-bold text-red-700 mb-2">变式题生成失败</div>' +
                '<p class="text-red-600 mb-4">AI 模型暂时不可用（可能是网络波动或服务繁忙），请稍后重试。</p>' +
                '<div class="flex space-x-4">' +
                '<button type="button" onclick="retryGeneration(\'' + keyPoint.replace(/'/g, "\\'") + '\')" class="bg-blue-500 text-white px-6 py-2 rounded-lg hover:bg-blue-600 font-bold">重新生成</button>' +
                '<button type="button" onclick="backToList()" class="bg-gray-500 text-white px-4 py-2 rounded-lg hover:bg-gray-600">返回知识点列表</button>' +
                '</div></div>';
            return;
        }

        document.getElementById('totalPractice').textContent = practiceQuestions.length;
        displayPracticeQuestion();
    }

    function updateAnsweredCount() {
        // 只统计真正有值的答案（排除空字符串和 undefined）
        const answered = Object.keys(practiceAnswers).filter(function(k) {
            const ans = practiceAnswers[k];
            return ans && ans.length > 0;
        }).length;
        document.getElementById('answeredCount').textContent = answered;
    }

    function updatePracticeControls() {
        document.getElementById('questionProgress').textContent = (currentPracticeIndex + 1) + '/' + practiceQuestions.length;
        const progress = ((currentPracticeIndex + 1) / practiceQuestions.length) * 100;
        document.getElementById('practiceProgressBar').style.width = progress + '%';
        document.getElementById('prevQuestion').style.visibility = currentPracticeIndex > 0 ? 'visible' : 'hidden';
        document.getElementById('submitPracticeBtn').textContent = currentPracticeIndex < practiceQuestions.length - 1 ? '下一题' : '提交练习';
        updateAnsweredCount();
    }

    function setPracticeConfidence(idx, level) {
        const currentConfidence = practiceConfidence[idx];

        // 如果已选中相同等级，则取消选择
        if (currentConfidence === level) {
            delete practiceConfidence[idx];
            clearPracticeFuzzyOptions(idx);
            renderPracticeConfidenceState(idx);
            if (shouldRecordPracticeQuestion(idx)) recordDetailQuestionAnswer(idx);
            return;
        }

        practiceConfidence[idx] = level;
        if (level !== 'unsure') {
            clearPracticeFuzzyOptions(idx);
        }
        renderPracticeConfidenceState(idx);
        if (shouldRecordPracticeQuestion(idx)) recordDetailQuestionAnswer(idx);
    }

    function togglePracticeFuzzyOption(idx, opt) {
        const option = normalizeOptionLetter(opt);
        if (!option) return;

        if (practiceConfidence[idx] !== 'unsure') {
            practiceConfidence[idx] = 'unsure';
        }

        const current = getPracticeFuzzyOptions(idx);
        if (current.includes(option)) {
            setPracticeFuzzyOptions(idx, current.filter(function(item) { return item !== option; }));
        } else {
            current.push(option);
            setPracticeFuzzyOptions(idx, current);
        }

        renderPracticeConfidenceState(idx);
        if (shouldRecordPracticeQuestion(idx)) recordDetailQuestionAnswer(idx);
    }

    
function submitPractice() {
        let correctCount = 0;
        const details = [];

        practiceQuestions.forEach(function(q, i) {
            // 清理答案格式：去除空格、点号、逗号等，只保留字母
            const cleanAnswer = function(ans) {
                return (ans || '').toString().toUpperCase().replace(/[^A-E]/g, '');
            };

            const userAns = cleanAnswer(practiceAnswers[i]);
            const correctAns = cleanAnswer(q.correct_answer);
            const isMultiple = (q.type || 'A1') === 'X';

            // 比对答案
            const isCorrect = isMultiple
                ? userAns.split('').sort().join('') === correctAns.split('').sort().join('')
                : userAns === correctAns;

            if (isCorrect) correctCount++;
            details.push({
                question: q.question,
                type: q.type || 'A1',
                difficulty: q.difficulty || '基础',
                options: q.options,
                user_answer: userAns,
                correct_answer: correctAns,
                is_correct: isCorrect,
                explanation: q.explanation,
                confidence: practiceConfidence[i] || '',
                fuzzy_options: getPracticeFuzzyOptions(i),
                option_texts: getPracticeFuzzyOptions(i).reduce(function(acc, opt) {
                    acc[opt] = (q.options && q.options[opt]) || ('选项' + opt);
                    return acc;
                }, {})
            });
        });

        const total = practiceQuestions.length;
        const score = total > 0 ? Math.round((correctCount / total) * 100) : 0;
        lastPracticeResult = {
            knowledge_point: currentKnowledge || '',
            score: score,
            correct_count: correctCount,
            wrong_count: total - correctCount,
            total: total,
            details: details
        };

        document.getElementById('questionArea').classList.add('hidden');
        document.getElementById('resultArea').classList.remove('hidden');
        document.getElementById('resultKnowledgeTitle').textContent = currentKnowledge || '未命名知识点';
        document.getElementById('practiceScore').textContent = score;
        document.getElementById('practiceCorrect').textContent = correctCount;
        document.getElementById('practiceWrong').textContent = total - correctCount;
        document.getElementById('practiceEmoji').textContent = score >= 80 ? '🏆' : score >= 60 ? '👍' : '📚';

        completeDetailTrackingSession(score, total);

        const container = document.getElementById('practiceDetails');
        container.innerHTML = '';

        details.forEach(function(d, i) {
            const div = document.createElement('div');
            div.className = 'p-6';
            const explanationId = 'detailExplanation-' + i;
            const review = analyzePracticeAnswer(d);
            const questionText = escapeHtml(d.question || '').replace(/\n/g, '<br>');
            const confidenceHtml =
                '<div class="mb-3 text-sm text-gray-600">' +
                '<span class="font-medium text-gray-700">自信度：</span>' + getConfidenceLabel(d.confidence) +
                '</div>';

            div.innerHTML =
                '<div class="flex items-center justify-between mb-3 gap-3">' +
                '<div class="flex flex-wrap items-center gap-2">' +
                '<span class="font-bold">第' + (i + 1) + '题</span>' +
                '<span class="' + (d.is_correct ? 'text-green-600' : 'text-red-600') + ' font-bold">' + (d.is_correct ? '✓ 正确' : '✗ 错误') + '</span>' +
                (!d.is_correct ? '<span class="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-bold ' +
                    (review.issueTone === 'warning' ? 'bg-yellow-100 text-yellow-800' :
                        review.issueTone === 'neutral' ? 'bg-gray-100 text-gray-700' : 'bg-red-100 text-red-700') + '">' +
                    escapeHtml(review.issueLabel) + '</span>' : '') +
                '</div>' +
                '<span class="text-sm text-gray-500">' + d.type + (d.type === 'X' ? '（多选）' : '') + ' · ' + d.difficulty + '</span>' +
                '</div>' +
                '<p class="mb-3 text-gray-800 leading-7">' + questionText + '</p>' +
                buildPracticeAnswerSummaryHtml(review) +
                buildPracticeAnswerIssueHtml(review) +
                buildPracticeAnswerOptionsHtml(d, review) +
                confidenceHtml +
                buildPracticeFuzzyDetailHtml(d) +
                buildMaskedExplanationHtml(d.explanation, d.is_correct, d.confidence, explanationId);
            container.appendChild(div);
        });
        return;

        details.forEach(function(d, i) {
            const userAnswer = d.user_answer || '';
            const correctAnswer = d.correct_answer || '';
            const optionLetters = getPracticeOptionLetters({ options: d.options || {} });
            let optionsHtml = '';

            optionLetters.forEach(function(opt) {
                const isSelected = userAnswer.includes(opt);
                const isCorrectOpt = correctAnswer.includes(opt);
                let cls = 'p-2 rounded mb-1 ';
                if (isCorrectOpt) cls += 'bg-green-100 text-green-800 border border-green-300';
                else if (isSelected && !isCorrectOpt) cls += 'bg-red-100 text-red-800 border border-red-300';
                else cls += 'text-gray-600';

                optionsHtml += '<div class="' + cls + '"><span class="font-bold">' + opt + '.</span> ' + ((d.options && d.options[opt]) || ('选项' + opt)) +
                    (isCorrectOpt ? ' ✓' : '') +
                    (isSelected && !isCorrectOpt ? ' ✗（你的答案）' : '') + '</div>';
            });

            const div = document.createElement('div');
            div.className = 'p-6';
            const explanationId = 'detailExplanation-' + i;
            const confidenceHtml =
                '<div class="mb-3 text-sm text-gray-600">' +
                '<span class="font-medium text-gray-700">自信度：</span>' + getConfidenceLabel(d.confidence) +
                '</div>';
            div.innerHTML =
                '<div class="flex items-center justify-between mb-3">' +
                '<div class="flex items-center space-x-2">' +
                '<span class="font-bold">第' + (i + 1) + '题</span>' +
                '<span class="' + (d.is_correct ? 'text-green-600' : 'text-red-600') + ' font-bold">' + (d.is_correct ? '✓ 正确' : '✗ 错误') + '</span>' +
                '</div>' +
                '<span class="text-sm text-gray-500">' + d.type + (d.type === 'X' ? '（多选）' : '') + ' · ' + d.difficulty + '</span>' +
                '</div>' +
                '<p class="mb-3 text-gray-800">' + escapeHtml(d.question) + '</p>' +
                '<div class="mb-4">' + optionsHtml + '</div>' +
                confidenceHtml +
                buildPracticeFuzzyDetailHtml(d) +
                buildMaskedExplanationHtml(d.explanation, d.is_correct, d.confidence, explanationId);
            container.appendChild(div);
        });
    }

    function copyPracticeSummary() {
        if (!lastPracticeResult || !Array.isArray(lastPracticeResult.details) || lastPracticeResult.details.length === 0) {
            alert('暂无可复制的知识点总结。');
            return;
        }

        let practiceReport = '';
        practiceReport += '==============================\n';
        practiceReport += '细节强化知识点总结\n';
        practiceReport += '==============================\n\n';
        practiceReport += '【基本信息】\n';
        practiceReport += '知识点：' + (lastPracticeResult.knowledge_point || '未命名知识点') + '\n';
        practiceReport += '练习时间：' + new Date().toLocaleString('zh-CN') + '\n';
        practiceReport += '得分：' + lastPracticeResult.score + ' 分\n';
        practiceReport += '正确：' + lastPracticeResult.correct_count + ' 题 / 错误：' + lastPracticeResult.wrong_count + ' 题 / 总计：' + lastPracticeResult.total + ' 题\n\n';
        practiceReport += '【题目详情】\n\n';

        lastPracticeResult.details.forEach(function(detail, index) {
            const review = analyzePracticeAnswer(detail);
            practiceReport += '第 ' + (index + 1) + ' 题 [' + (detail.type || '') + '] [' + (detail.difficulty || '') + ']\n';
            practiceReport += (detail.question || '') + '\n\n';

            getPracticeOptionLetters(detail).forEach(function(opt) {
                const isSelected = review.userLetters.includes(opt);
                const isCorrect = review.correctLetters.includes(opt);
                let mark = '[ ]';
                if (isCorrect && isSelected) mark = '[选对]';
                else if (isCorrect) mark = '[漏选]';
                else if (isSelected) mark = detail.type === 'X' ? '[多选]' : '[错选]';
                practiceReport += mark + ' ' + opt + '. ' + (((detail.options || {})[opt]) || ('选项' + opt)) + '\n';
            });

            practiceReport += '\n';
            practiceReport += '你的答案：' + formatPracticeAnswerLetters(review.userLetters, '未作答') + '\n';
            practiceReport += '正确答案：' + formatPracticeAnswerLetters(review.correctLetters, '无') + '\n';
            if (!detail.is_correct) {
                practiceReport += '错因：' + review.issueLabel + '\n';
                if (review.missingLetters.length > 0) {
                    practiceReport += '漏选项：' + formatPracticeAnswerLetters(review.missingLetters) + '\n';
                }
                if (review.extraLetters.length > 0) {
                    practiceReport += (detail.type === 'X' ? '多选项：' : '错选项：') + formatPracticeAnswerLetters(review.extraLetters) + '\n';
                }
            }
            practiceReport += '自信度：' + getConfidenceLabel(detail.confidence) + '\n';
            practiceReport += buildPracticeFuzzyReportSection(detail);
            practiceReport += '结果：' + (detail.is_correct ? '✓ 正确' : '✗ 错误') + '\n\n';
            practiceReport += '解析：' + (detail.explanation || '无解析') + '\n';
            practiceReport += '------------------------------------------------------------\n\n';
        });

        practiceReport += '报告生成时间：' + new Date().toLocaleString('zh-CN') + '\n';

        navigator.clipboard.writeText(practiceReport).then(function() {
            const btn = document.getElementById('copyPracticeSummaryBtn');
            const text = document.getElementById('copyPracticeSummaryText');
            const originalText = text.textContent;

            btn.classList.remove('bg-indigo-500', 'hover:bg-indigo-600');
            btn.classList.add('bg-green-500', 'hover:bg-green-600');
            text.textContent = '已复制到剪贴板';

            setTimeout(function() {
                btn.classList.remove('bg-green-500', 'hover:bg-green-600');
                btn.classList.add('bg-indigo-500', 'hover:bg-indigo-600');
                text.textContent = originalText;
            }, 2000);
        }).catch(function(err) {
            console.error('复制失败:', err);
            alert('复制失败，请手动复制以下内容：\n\n' + practiceReport.substring(0, 500) + '...');
        });
        return;

        let report = '';
        report += '==============================\n';
        report += '细节强化知识点总结\n';
        report += '==============================\n\n';
        report += '【基本信息】\n';
        report += '知识点：' + (lastPracticeResult.knowledge_point || '未命名知识点') + '\n';
        report += '练习时间：' + new Date().toLocaleString('zh-CN') + '\n';
        report += '得分：' + lastPracticeResult.score + ' 分\n';
        report += '正确：' + lastPracticeResult.correct_count + ' 题 / 错误：' + lastPracticeResult.wrong_count + ' 题 / 总计：' + lastPracticeResult.total + ' 题\n\n';
        report += '【题目详情】\n\n';

        lastPracticeResult.details.forEach(function(detail, index) {
            const userAnswer = detail.user_answer || '';
            const correctAnswer = detail.correct_answer || '';
            report += '第 ' + (index + 1) + ' 题 [' + (detail.type || '') + '] [' + (detail.difficulty || '') + ']\n';
            report += (detail.question || '') + '\n\n';

            getPracticeOptionLetters({ options: detail.options || {} }).forEach(function(opt) {
                const isCorrectOpt = correctAnswer.includes(opt);
                const isSelected = userAnswer.includes(opt);
                let mark = '  ';
                if (isCorrectOpt) mark = '✓ ';
                else if (isSelected && !isCorrectOpt) mark = '✗ ';
                report += mark + opt + '. ' + (((detail.options || {})[opt]) || ('选项' + opt)) + '\n';
            });

            report += '\n';
            report += '你的答案：' + (userAnswer || '未作答') + '\n';
            report += '正确答案：' + correctAnswer + '\n';
            report += '自信度：' + getConfidenceLabel(detail.confidence) + '\n';
            report += buildPracticeFuzzyReportSection(detail);
            report += '结果：' + (detail.is_correct ? '✓ 正确' : '✗ 错误') + '\n\n';
            report += '解析：' + (detail.explanation || '无解析') + '\n';
            report += '------------------------------------------------------------\n\n';
        });

        report += '报告生成时间：' + new Date().toLocaleString('zh-CN') + '\n';

        navigator.clipboard.writeText(report).then(function() {
            const btn = document.getElementById('copyPracticeSummaryBtn');
            const text = document.getElementById('copyPracticeSummaryText');
            const originalText = text.textContent;

            btn.classList.remove('bg-indigo-500', 'hover:bg-indigo-600');
            btn.classList.add('bg-green-500', 'hover:bg-green-600');
            text.textContent = '✅ 已复制到剪贴板';

            setTimeout(function() {
                btn.classList.remove('bg-green-500', 'hover:bg-green-600');
                btn.classList.add('bg-indigo-500', 'hover:bg-indigo-600');
                text.textContent = originalText;
            }, 2000);
        }).catch(function(err) {
            console.error('复制失败:', err);
            alert('复制失败，请手动复制以下内容：\n\n' + report.substring(0, 500) + '...');
        });
    }

    
const elements = {};
function createClassList(target) {
  return {
    add: function() {
      const set = new Set((target.className || '').split(/\s+/).filter(Boolean));
      Array.from(arguments).forEach(function(cls) { set.add(cls); });
      target.className = Array.from(set).join(' ');
    },
    remove: function() {
      const removeSet = new Set(Array.from(arguments));
      target.className = (target.className || '')
        .split(/\s+/)
        .filter(function(cls) { return cls && !removeSet.has(cls); })
        .join(' ');
    },
    contains: function(cls) {
      return (target.className || '').split(/\s+/).filter(Boolean).includes(cls);
    }
  };
}
function registerElement(id, className) {
  const element = { id: id, textContent: '', innerHTML: '', className: className || '', children: [], appendChild: function(child) { this.children.push(child); } };
  element.classList = createClassList(element);
  elements[id] = element;
  return element;
}
const document = {
  getElementById: function(id) { return Object.prototype.hasOwnProperty.call(elements, id) ? elements[id] : null; },
  createElement: function() { const element = { className: '', innerHTML: '', children: [], appendChild: function(child) { this.children.push(child); } }; element.classList = createClassList(element); return element; }
};
globalThis.document = document;
globalThis.alert = function(message) { throw new Error('Unexpected alert: ' + message); };
globalThis.setTimeout = function(fn) { fn(); return 0; };
let copiedText = '';
Object.defineProperty(globalThis, 'navigator', { value: { clipboard: { writeText: function(text) { copiedText = text; return Promise.resolve(); } } }, configurable: true });
function escapeHtml(text) { return text || ''; }
function buildMaskedExplanationHtml() { return '<div>??</div>'; }
function completeDetailTrackingSession() { return Promise.resolve(); }
let currentKnowledge = '????';
let practiceQuestions = [{ question: '??????????????', type: 'A1', difficulty: '??', options: { A: '??A??', B: '??B??', C: '??C??', D: '??D??', E: '??E??' }, correct_answer: 'C', explanation: '????' }];
let currentPracticeIndex = 0;
let practiceAnswers = { 0: 'D' };
let practiceConfidence = {};
let practiceFuzzyOptions = {};
let lastPracticeResult = null;
registerElement('practice-btn-sure-0');
registerElement('practice-btn-unsure-0');
registerElement('practice-btn-no-0');
registerElement('practice-confidence-display-0', 'mt-2 text-sm text-center hidden');
registerElement('practice-fuzzy-panel-0', 'hidden');
registerElement('practice-fuzzy-summary-0');
registerElement('practice-fuzzy-opt-0-A');
registerElement('practice-fuzzy-opt-0-B');
registerElement('practice-fuzzy-opt-0-C');
registerElement('practice-fuzzy-opt-0-D');
registerElement('practice-fuzzy-opt-0-E');
registerElement('questionArea');
registerElement('resultArea', 'hidden');
registerElement('resultKnowledgeTitle');
registerElement('practiceScore');
registerElement('practiceCorrect');
registerElement('practiceWrong');
registerElement('practiceEmoji');
registerElement('practiceDetails');
registerElement('copyPracticeSummaryBtn', 'bg-indigo-500 hover:bg-indigo-600');
registerElement('copyPracticeSummaryText');
elements.copyPracticeSummaryText.textContent = '??????????';
(async function main() {
  setPracticeConfidence(0, 'unsure');
  tryTogglePracticeFuzzyOptionByShortcut(0, '4');
  tryTogglePracticeFuzzyOptionByShortcut(0, '3');
  submitPractice();
  copyPracticeSummary();
  await Promise.resolve();
  await Promise.resolve();
  console.log(JSON.stringify({ copiedText: copiedText, detailHtml: elements.practiceDetails.children[0].innerHTML, btnText: elements.copyPracticeSummaryText.textContent, btnClass: elements.copyPracticeSummaryBtn.className }));
})().catch(function(error) { console.error('ERRNAME', error && error.name); console.error('ERRMSG', error && error.message); console.error(error && error.stack); process.exit(1); });
