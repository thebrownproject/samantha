import XCTest
@testable import Samantha

final class SamanthaTests: XCTestCase {
    func testAppStateEnumCasesExist() {
        let cases: [AppState] = [.idle, .listening, .thinking, .speaking, .error]
        XCTAssertEqual(cases.count, 5)
        XCTAssertEqual(AppState.allCases.count, cases.count)
    }
}
