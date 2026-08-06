function answerMeTheseQuestionsThree() {
  return [
    'What is your name?',
    'What is your quest?',
    randomChoice([
      'What is your favorite color?',
      'What is the capital of Assyria?',
      'What is the air-speed velocity of an unladen swallow?',
    ])
  ]
}

function randomChoice(choices) {
  return choices[Math.floor(Math.random() * choices.length)]
}

module.exports = { answerMeTheseQuestionsThree }
